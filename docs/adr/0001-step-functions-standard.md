# ADR-0001: Orchestrate with Step Functions Standard workflows
Status: accepted · 2026-10-18

## Context
The pipeline has 6–8 steps and branches by input format. It must also be able to **pause for hours or days** while a clerk reviews an exception. Operators need a per-invoice history of what happened.

## Decision
Use AWS Step Functions **Standard** workflows, with one execution per invoice document. Human review uses the `.waitForTaskToken` callback pattern.

## Alternatives
- **Express workflows.** Cheaper per transition, but limited to 5 minutes and with no callback tokens, so review would have to be split into a second workflow.
- **SQS chaining between Lambdas.** Lowest cost, but retries, branching, timeouts and the per-invoice history would all have to be hand-built.
- **Temporal / Airflow.** Very capable, but they mean extra infrastructure to run, which is unjustified at this scale.

## Implementation note (2026-10-19)
- The definition is `infra/terraform/modules/pipeline/asl.json.tftpl`.
- The step order is tested against `application.pipeline.AUTOMATIC_STEPS`.
- The review wait uses `lambda:invoke.waitForTaskToken`: the token is stored on the invoice record, and the review API (W5) answers with `{"decision": "approved"|"rejected"}`.
- The review timeout is 30 days.
- `include_execution_data` is off, so execution payloads never reach the logs.

## Consequences
- ➕ Built-in retries with backoff, catch → DLQ, and a visual execution history.
- ➕ Execution names are unique per state machine (for 90 days), so they double as the ingest idempotency key (ADR-0007).
- ➖ Billed per state transition, which is more expensive than Express at high volume. `docs/cost.md` will quantify this.
- ➖ The state machine definition (ASL) is JSON. It is kept in Terraform templates and covered by tests.
