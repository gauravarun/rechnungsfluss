# ADR-0004: Extract plain PDFs with Claude on Bedrock using tool-use schemas
Status: accepted · 2026-10-18

## Context
Plain PDFs have no structured data. Layouts vary by supplier, and some PDFs are scans.

## Decision
- Send the PDF (document block) to a Claude model on Amazon Bedrock.
- Force a single tool call whose input schema is the canonical extraction schema. The model must also return per-field confidence and "not found" explicitly, rather than guessing.
- Prompts are versioned files under `src/rechnungsfluss/extraction/prompts/`.
- The **model ID is pinned in config** after checking availability in `eu-central-1` (an EU inference profile if needed). Any model change requires a new eval run.

## Alternatives
- **Amazon Textract + rules.** Cheaper and deterministic, but brittle across layouts, and every supplier template needs rule maintenance.
- **Open-source OCR + small model.** Self-hosting cost and much more evaluation effort.

## Consequences
- ➕ Handles varied layouts. Structured output means no free-text parsing.
- ➖ Non-deterministic output and per-token cost. Mitigations: the eval harness (ADR-0010), a cost alarm, and **the LLM never auto-approves alone**. Validation, matching and confidence thresholds still decide the routing.
- ➖ Invoice content goes to Bedrock. That's acceptable here because all data is synthetic. With real data it would need a DPA review and EU-region processing.

## Amendment 2026-10-19: implementation choices
- **Structured outputs, not a forced tool call.** Claude Opus 5.5 rejects forced `tool_choice` with a 400. The extractor calls `beta.messages.parse(output_format=ExtractedInvoice)`, so the response is guaranteed to match the schema. Bedrock supports both structured outputs and PDF document input.
- **Model:** `anthropic.claude-opus-5-5` through the SDK's `AnthropicBedrockMantle` client (anthropic 1.11.0). It can be overridden with `RF_BEDROCK_MODEL_ID`, for example for an EU inference profile. Availability in the owner's region is checked during setup (runbook), not assumed.
- **Effort `medium`**, set explicitly: Opus 5.5 also defaults to medium, but the setting is now visible and pinned. Thinking is adaptive and can't be disabled on this model. An effort sweep follows the first baseline.
- **Refusals:** Bedrock has no server-side fallbacks, so the SDK's client-side `BetaRefusalFallbackMiddleware` retries a refused request on `anthropic.claude-opus-4-8`. A refusal that survives the fallback raises `ExtractionRefusedError`. That's a technical failure (`FAILED`, replayable), never an approval.
- **The model returns a print-oriented schema** (amounts and dates as strings). `extraction/mapping.py` parses and normalizes them. It never fills a gap by calculation, with one documented exception: BT-106 = BT-109, because the model has no allowances or charges.
- **Routing:** missing data → `extraction_incomplete`, flagged doubts → `extraction_uncertain`, not an invoice → `not_an_invoice`. All of these go to review. The model never decides routing, and the extracted invoice gets the same KoSIT validation as native XML (ADR-0002).
- **Prompt-injection stance:** document text is declared data in the prompt, the output is schema-constrained, and approval logic never reads free text from the model.
- **Record/replay:** every live call can be recorded as a cassette keyed by (PDF hash, prompt version, model, effort). PR CI replays them without credentials (ADR-0010).
