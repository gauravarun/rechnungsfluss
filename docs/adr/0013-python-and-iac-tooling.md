# ADR-0013: Tooling: uv, ruff, mypy strict, pytest; Terraform + tflint + checkov
Status: accepted · 2026-10-18

## Decision
- **Python:** 3.12, with `uv` for the lockfile and venv, `ruff` (lint + format), `mypy --strict` and `pytest` (+ `hypothesis` for property tests).
- **Frontend:** Vite + React + TypeScript, `eslint`, `tsc --noEmit`, Playwright.
- **IaC:** Terraform ≥ 1.9 with the AWS provider pinned, plus `tflint` and `checkov` in CI.
- **Secrets hygiene:** `gitleaks` in pre-commit and CI.
- **Pinning:** dependencies are pinned to releases at least two weeks old at the time they are added.

## Consequences
- ➖ `mypy --strict` slows early prototyping. Accepted, because the type checks are an explicit project rule.
