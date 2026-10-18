# Rechnungsfluss

An e-invoice processing platform for a **fictional** German mid-sized company. It ingests XRechnung, ZUGFeRD / Factur-X and plain-PDF invoices, then for each invoice it:
1. Validates it with the official tooling.
2. Matches it against purchase orders.
3. Routes exceptions to a human review UI.
4. Exports approved invoices to a mock ERP.

> **Portfolio project.** The client, its suppliers, purchase orders and invoices are synthetic. No real personal or company data is used.

## Status
This README only states what exists and what has been measured. Plans live in [docs/brief.md](docs/brief.md).

| Area | Status |
|---|---|
| Brief, architecture, ADRs | ✅ written (`docs/`) |
| Canonical EN 16931 model, state machine, audit chain | ✅ built, unit-tested (structural validation only) |
| Local-mode storage adapters (filesystem, SQLite) | ✅ built, unit-tested; pipeline runner not built (W3) |
| XRechnung / ZUGFeRD parsing, KoSIT validation | ⏳ not built (W2) |
| Synthetic invoice generator (≥ 100 labelled) | ⏳ not built (W2) |
| AWS infra (Terraform) | 🚧 S3, DynamoDB, KMS only; validated in CI, **never applied** |
| Step Functions pipeline, Lambdas, queues | ⏳ not built (W3) |
| Bedrock extraction, eval harness | ⏳ not built (W4) |
| Review UI | 🚧 React + TS shell only (lint, types, tests in CI); review flows not built (W5) |
| Mock ERP | ⏳ not built (W5) |
| Runbook | 🚧 deploy bootstrap only |
| Alarms, threat model, cost estimate | ⏳ not built (W6) |

## Measured results
**None yet.** Results will appear here only after the evaluation harness exists (W4). The table will be generated from the files in `eval/reports/`, and each number will link to the report it comes from.

## What is simulated
- **Client, tenants, suppliers, POs, invoices:** all synthetic.
- **ERP:** a mock FastAPI service with fault injection.
- **Authentication:** signed dev tokens locally; Cognito in the AWS environment.
- **AWS:** nothing has been deployed. Terraform is only statically checked (validate, tflint, checkov). The planned local mode runs the pipeline in-process without an AWS account.

## Known limits
- No real-world invoices have been tested. Synthetic data is cleaner and more uniform than real supplier invoices, so measured accuracy will overstate real-world accuracy.
- Legal statements in [docs/standards.md](docs/standards.md) marked ⚠️ are not yet verified against primary sources.
- Tenant isolation is currently enforced and tested in application code only. The IAM and KMS layers (ADR-0006) are not built yet, and they can only be verified in a real AWS account; moto does not evaluate IAM conditions.

## Getting started
Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Docker (for the KoSIT validator from W2), Node 20+ (for `web/`).

```bash
cp .env.example .env
make install
make check   # lint, types, unit tests
make web-install web-check   # review UI: lint, types, tests, build
```

## Documentation
- [Project brief](docs/brief.md)
- [Architecture](docs/architecture.md)
- [Standards and sources](docs/standards.md)
- [Architecture decision records](docs/adr/)
- [Decisions log](docs/DECISIONS.md)
- [Changelog](docs/CHANGELOG.md)

## Licence
MIT. See [LICENSE](LICENSE).
