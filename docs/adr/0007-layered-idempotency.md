# ADR-0007: Layered idempotency
Status: accepted · 2026-10-18 · amended 2026-10-19 (layer 2)

## Context
SQS and EventBridge deliver at least once. Lambdas retry, operators replay, and the ERP must never book the same invoice twice.

## Decision
| Layer | Key | Mechanism |
|---|---|---|
| Ingest | `tenant + sha256(file)` | Used as the Step Functions execution name; duplicate names are rejected |
| Each step | invoice state + version | The step runs only from its source state; the write is conditional on the version read (DynamoDB `ConditionExpression`). A retried or concurrent step is a no-op or fails with a conflict, never a double effect. |
| Export | `tenant + invoice_id + version` | `Idempotency-Key` HTTP header; the mock ERP stores keys and returns the original response |

*Amendment 2026-10-19:* layer 2 was planned as Powertools `@idempotent`. The state machine already makes every step state-guarded, and the conditional write gives the same guarantee without a second store, so Powertools idempotency was dropped. The `idempotency` table now holds only the ingest claims, with a 90-day TTL.

## Consequences
- ➕ Replay is safe by construction, so the replay tool can simply re-drive.
- ➖ The same file re-sent **on purpose** (e.g. after a rejection) needs an explicit "reprocess" action that adds a nonce. It goes through the replay CLI and is audited.
- Note: idempotency is *technical* dedupe of the same bytes. *Business* duplicates (same invoice, different file) are covered by ADR-0008.
