# ADR-0010: Recorded eval on PRs, live eval nightly
Status: accepted · 2026-10-18

## Context
The build must fail if extraction accuracy drops. Calling Bedrock on every PR needs AWS credentials in CI, costs money, and is non-deterministic.

## Decision
- **PR job (`eval-recorded`):** replays recorded Bedrock responses ("cassettes") through the full post-extraction pipeline. It catches regressions in parsing, normalization, validation, matching and routing deterministically.
- **Nightly / manual job (`eval-live`):** calls Bedrock via GitHub OIDC with a narrowly scoped IAM role. It re-records cassettes and gates on live accuracy.
- **Thresholds** live in `eval/thresholds.yaml`. Until the owner sets them after the W4 baseline, the gate fails on any drop of more than 2 percentage points versus the committed baseline.
- **README numbers** come only from live reports, with date, model ID and prompt version.

## Consequences
- ➕ Fast, free, deterministic PR checks.
- ➖ PR runs don't detect model drift; nightly runs do. This is stated in the README.
