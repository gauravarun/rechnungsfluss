# ADR-0006: Pooled multi-tenancy enforced by IAM ABAC and per-tenant KMS keys
Status: accepted · 2026-10-18

## Context
Two tenants need strict data separation, and it must be tested, not just asserted.

## Decision
A pooled model (shared tables, buckets and functions), with isolation enforced in **three layers**:
1. **Application:** the tenant comes only from the authenticated context (JWT claim, or the S3 key prefix at ingest). Every repository method requires a `TenantId`, and keys are built by one function.
2. **IAM:** handlers assume a tenant-scoped role with session tag `tenant=<id>`. Policies use `dynamodb:LeadingKeys` matching `TENANT#${aws:PrincipalTag/tenant}#*` and an S3 prefix condition on `${aws:PrincipalTag/tenant}/`.
3. **Encryption:** one KMS key per tenant for S3 objects. The key policy allows only that tenant's role.

## Alternatives
- **Silo (an account or stack per tenant).** The strongest isolation, but the infrastructure and cost multiply per tenant.
- **Application-only checks.** Cheap, but a single bug leaks data.

## Consequences
- ➕ Cheap to run. A bug in layer 1 is still caught by layers 2 and 3 in AWS.
- ➖ IAM policy complexity. Mitigation: tests of the policy documents, plus cross-tenant tests at the unit, integration and Playwright levels.
- ➖ In local mode only layer 1 applies, and moto doesn't evaluate these IAM conditions (ADR-0009). Layers 2 and 3 can only be verified in a real sandbox account. This is documented as a known limit.
