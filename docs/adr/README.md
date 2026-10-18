# Architecture decision records

Format: context → decision → consequences (trade-offs). Status values are `proposed`, `accepted` and `superseded by NNNN`.

| # | Title | Status |
|---|---|---|
| [0001](0001-step-functions-standard.md) | Orchestrate with Step Functions Standard workflows | accepted |
| [0002](0002-canonical-en16931-model.md) | One canonical EN 16931 model; LLM output validated as CII | accepted |
| [0003](0003-kosit-validator-in-lambda-container.md) | Run the KoSIT validator in a Lambda container image | accepted |
| [0004](0004-bedrock-claude-tool-use-extraction.md) | Extract plain PDFs with Claude on Bedrock using tool-use schemas | accepted |
| [0005](0005-dynamodb-single-table-and-audit-table.md) | DynamoDB single table + separate append-only audit table | accepted |
| [0006](0006-pooled-multi-tenancy-abac.md) | Pooled multi-tenancy enforced by IAM ABAC and per-tenant KMS keys | accepted |
| [0007](0007-layered-idempotency.md) | Layered idempotency | accepted |
| [0008](0008-duplicate-detection.md) | Duplicates go to review, never auto-reject | accepted |
| [0009](0009-local-mode-ports-adapters-no-localstack.md) | Local mode via ports/adapters; moto instead of LocalStack | accepted |
| [0010](0010-evaluation-strategy.md) | Recorded eval on PRs, live eval nightly | accepted |
| [0011](0011-langfuse-cloud-eu.md) | Langfuse Cloud (EU) for LLM tracing | accepted |
| [0012](0012-review-ui-auth.md) | Review UI auth: Cognito in AWS, signed dev tokens locally | accepted |
| [0013](0013-python-and-iac-tooling.md) | Tooling: uv, ruff, mypy strict, pytest; Terraform + tflint + checkov | accepted |
