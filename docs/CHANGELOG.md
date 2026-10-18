# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project has no releases yet; all changes are listed under *Unreleased*.

## [Unreleased]

### Added (session 1, continued)
- `web/`: Vite + React 19 + TypeScript review-UI shell, ESLint (typescript-eslint strict, type-checked), Vitest + Testing Library; CI job for lint, types, tests and build.
- Repository settings: auto-merge allowed; merged branches deleted automatically.

### Added (session 1, 2026-10-18)
- **Docs:** project brief, architecture overview (Mermaid), standards and sources with pinned validator versions, ADR-0001 to ADR-0013, runbook (deploy bootstrap only).
- **Domain:**
  - Canonical EN 16931 invoice model (pydantic, BT/BG-referenced, structural validation only).
  - Invoice state machine.
  - Hash-chained audit events with tamper detection.
  - Tenant-scoped key builder that rejects separator injection.
- **Local adapters:**
  - Filesystem object store with path-escape protection.
  - SQLite invoice repository with optimistic concurrency.
  - SQLite audit log, append-only by triggers.
- **Infra:** Terraform modules for per-tenant KMS keys, the document bucket and the DynamoDB tables (`envs/aws-dev`). Not applied anywhere.
- **CI:**
  - ruff, mypy `--strict` (src + tests), pytest with a 90 % coverage floor.
  - gitleaks on full history, plus a planted-key self-test.
  - Terraform fmt / validate / tflint / checkov.
- **Repo hygiene:** pre-commit (ruff, gitleaks, private-key detection), `.env.example`, LF line endings, MIT licence.

### Changed
- Repository published on GitHub (public) with branch protection on `main`. Commit emails rewritten to the GitHub noreply address.
- Local mode no longer uses LocalStack. It uses an in-process runner plus `moto` instead (ADR-0009).
