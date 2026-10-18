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
