# ADR-0005: DynamoDB single table + separate append-only audit table
Status: accepted · 2026-10-18

## Context
Access patterns are known and key-based: get an invoice, list open reviews per tenant, look up a PO, look up the duplicate key. The audit log must be append-only.

## Decision
- `rf-main`: single-table design, with every key prefixed `TENANT#<id>`.
- `rf-audit`: a separate table. Writers' IAM policies allow only `PutItem` guarded by `attribute_not_exists` (no Update or Delete). Each event stores the SHA-256 of the previous event of the same invoice (hash chain).
- `rf-idempotency`: owned by Powertools idempotency, with TTL.

## Alternatives
- **Aurora Serverless (PostgreSQL).** Flexible SQL reporting, but a VPC, connection management, a higher idle cost, and row-level security instead of IAM-level tenant isolation.

## Consequences
- ➕ Serverless and pay-per-request. IAM `LeadingKeys` enables tenant isolation at the database layer.
- ➖ Ad-hoc reporting is harder. If needed later: DynamoDB export to S3 + Athena.
- ➖ The hash chain gives tamper *evidence*, not tamper *proof*. That is documented in `security.md`.
