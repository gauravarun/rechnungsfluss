# ADR-0007: Layered idempotency
Status: accepted · 2026-10-18

## Context
SQS and EventBridge deliver at least once. Lambdas retry, operators replay, and the ERP must never book the same invoice twice.

## Decision
| Layer | Key | Mechanism |
|---|---|---|
| Ingest | `tenant + sha256(file)` | Used as the Step Functions execution name; duplicate names are rejected |
| Each step | `invoice_id + step + sha256(input)` | Powertools `@idempotent` on DynamoDB, with TTL |
| Export | `tenant + invoice_id + version` | `Idempotency-Key` HTTP header; the mock ERP stores keys and returns the original response |

## Consequences
- ➕ Replay is safe by construction, so the replay tool can simply re-drive.
- ➖ The same file re-sent **on purpose** (e.g. after a rejection) needs an explicit "reprocess" action that adds a nonce. It goes through the replay CLI and is audited.
- Note: idempotency is *technical* dedupe of the same bytes. *Business* duplicates (same invoice, different file) are covered by ADR-0008.
