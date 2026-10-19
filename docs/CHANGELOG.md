# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). This project has no releases yet; all changes are listed under *Unreleased*.

## [Unreleased]

### Added (session 2, 2026-10-19: W2)
- **Canonical model:** extended with postal addresses (BG-5/BG-8), seller contact (BG-6), electronic addresses (BT-34/BT-49), payment-means code (BT-81) and specification ID (BT-24). All are required for valid XRechnung.
- **`formats/cii`:** CII D16B serializer and parser with hardened XML parsing (no DTDs or entities, size limit). The round trip is property-tested.
- **`synthetic/builder`:** builds invoices whose totals follow the EN 16931 arithmetic rules.
- **`validation/kosit`:** KoSIT daemon client and report parser that returns the official rule IDs.
- **`scripts/kosit.sh`:** fetches the validator and configuration (SHA-256 pinned) and runs the daemon.
- **CI:** starts KoSIT and runs the integration tests with `RF_REQUIRE_KOSIT=1`, so a missing validator fails the build instead of skipping.
- **`formats/zugferd`:** extracts and embeds Factur-X/ZUGFeRD 2.x data with the `factur-x` library. The attachment is pre-checked by the hardened XML parser. MINIMUM and BASIC WL profiles are flagged as not being e-invoices.
- **`synthetic/render`:** deterministic invoice PDF (reportlab) with a visible "SYNTHETISCH" marker.
- **Dependency cutoff:** `[tool.uv] exclude-newer` enforces the two-week rule for transitive dependencies too. It downgraded `ast-serialize`, `iniconfig`, `pluggy` and `pillow`, which had slipped in too new.
- **`formats/ubl`:** UBL 2.1 Invoice serializer and parser. A property test checks that UBL and CII parse to identical canonical invoices.

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
