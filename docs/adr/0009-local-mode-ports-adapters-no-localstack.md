# ADR-0009: Local mode via ports/adapters; moto instead of LocalStack
Status: accepted · 2026-10-18 (revises the original plan, which named LocalStack)

## Context
The project must run without an AWS account. The plan proposed LocalStack. Checked on 2026-10-18:
- The `localstack/localstack` GitHub repository is **archived** (last push 2026-03-23; verified via the GitHub API).
- LocalStack's release notes state that an **auth token is required** from release 2026.03.0. The free tier is a "Hobby" plan for non-commercial use.

Requiring a third-party account to run a portfolio repo hurts reproducibility.

## Decision
- **Hexagonal design.** Every side effect goes through a port (`ObjectStore`, `InvoiceRepository`, `AuditLog`, `Queue`, `Extractor`, `Validator`, `ErpClient`).
- **`RF_MODE=local`:** an in-process pipeline runner with filesystem and SQLite adapters. Nothing else is needed except Docker for the KoSIT daemon.
- **AWS adapters** are integration-tested with **moto** (Apache-2.0, in-process).
- **Terraform** is checked in CI with `terraform validate`, `tflint` and `checkov`. The real `apply` happens only in a sandbox account the owner controls.

## Consequences
- ➕ `git clone && make install && make check` works with no accounts.
- ➖ The Step Functions ASL is not executed end-to-end locally; the local runner mirrors it. Mitigation: a test asserts that the local runner's step order and branches match the ASL definition.
- ➖ moto doesn't evaluate IAM policy conditions such as DynamoDB `LeadingKeys`. IAM tenant isolation can only be verified in a real sandbox account. This is documented as a known limit until then.
