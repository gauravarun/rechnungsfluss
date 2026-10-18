# ADR-0011: Langfuse Cloud (EU) for LLM tracing
Status: accepted · 2026-10-18

## Decision
Trace every Bedrock call in Langfuse Cloud, EU region. Each trace records prompt version, model ID, tokens, latency, cost, tenant, invoice ID and eval run ID.
- Keys are stored in AWS Secrets Manager (deployed) or `.env` (local).
- If the keys are absent, tracing is a **no-op**, so local mode and CI work offline.

## Alternatives
- **Self-hosted Langfuse v3.** It needs Postgres, ClickHouse, Redis and blob storage, which is heavy for a laptop.
- **CloudWatch only.** No prompt and response inspection, and no eval comparison UI.

## Consequences
- ➖ Prompt and response content leaves AWS. That's acceptable only because all data is synthetic. Before any real data: a DPA, or self-hosting, or masking. This is recorded in `security.md`.
