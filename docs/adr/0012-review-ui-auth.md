# ADR-0012: Review UI auth: Cognito in AWS, signed dev tokens locally
Status: accepted · 2026-10-18

## Decision
- **AWS:** a Cognito user pool, with a `custom:tenant` claim and a `role` claim (`clerk` | `lead` | `auditor`). API Gateway HTTP API has a JWT authorizer.
- **Local:** HS256 tokens signed with `RF_DEV_JWT_SECRET`, minted by `rf dev-token --tenant ... --role ...`. Both modes use one claims-validation module.
- **Tenant isolation:** the API derives the tenant only from the validated token. No endpoint accepts a tenant parameter.

## Consequences
- ➖ No production SSO (out of scope). Dev tokens are refused when `RF_MODE=aws`, enforced by a startup check and a test.
