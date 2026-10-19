# Decisions log

Short, dated entries per work session. Significant architectural decisions get a full ADR in [adr/](adr/).

## 2026-10-18: Session 1 (planning + W1)
### Owner decisions
- **Plan approved:** brief, architecture, 6-week milestones, 13 ADRs.
- **Stack:** Python 3.12 + FastAPI on AWS.
- **PO matching:** 2-way, with per-tenant tolerances. No goods receipts in v1.
- **Tracing:** Langfuse Cloud EU.
- **AI eval:** recorded responses on PRs, live eval nightly.
- **Accuracy threshold:** the owner sets it after the W4 baseline. Until then the gate blocks only regressions of more than 2 percentage points.

### Engineering decisions
- **LocalStack dropped** (ADR-0009). The upstream OSS repo was archived on 2026-03-23, and an auth token has been required since 2026.03.0 (verified via the GitHub API and LocalStack release notes). Replaced by the in-process local mode plus `moto`.
- **Validation versions pinned** (verified on GitHub, 2026-10-18):
  - KoSIT validator v1.6.3
  - XRechnung configuration v2026-08-31 (XRechnung 3.0.2)
  - EN 16931 artefacts validation-1.3.17
- **ZUGFeRD and PDF inputs:** XRechnung BR-DE-* findings are downgraded to warnings (ADR-0002).
- **Canonical model** does structural validation only. A test asserts that inconsistent totals are *accepted* by the model, so business rules can't creep into our code (ADR-0002).
- **Audit log** is append-only in two places: SQLite triggers locally, and IAM (PutItem only) in AWS, to be built in W3. The hash chain gives tamper evidence, not tamper proof.
- **Ports added only when needed.** Storage ports exist now. Queue, extractor, validator and ERP ports are added with their features, so no speculative interfaces are created.
- **Checkov skips (8):**
  - KMS key policies (3 checks × 2 policies). These are false positives: in a key policy, `"*"` means "this key".
  - S3 access logging, to be replaced by CloudTrail data events in W6.
  - Cross-region replication, out of scope.

  Each skip is justified inline.
- **Dependency pins:** every dependency is pinned to a release at least two weeks old. GitHub Actions are pinned by commit SHA.
- **Fictional client:** Nordwerk Industriebedarf GmbH, with tenants `nordwerk-de` and `nordwerk-at`.

### Repository hosting
- **Hosted publicly** at <https://github.com/gauravarun/rechnungsfluss>. Branch protection on GitHub's free plan requires a public repo.
- **Commit emails rewritten** to the GitHub noreply address before publishing, so no personal email is exposed in history.
  - The first private repo was deleted and recreated. GitHub keeps PR refs (`refs/pull/N/head`) permanently, and the PR #1 refs still held the old commits.
  - As a result, history before this point contains merges of simulated PRs (`--no-ff`), not GitHub PRs.
- **`main` protection:**
  - Changes go in only through PRs.
  - Required checks: `lint, types, unit tests`, `web lint, types, tests, build` and `secret scan`. The branch must be up to date before merging.
  - The rules apply to admins too.
  - No force-push or deletion. Conversations must be resolved.
  - **0 required approvals**, because GitHub doesn't let you approve your own PR, so solo work would get stuck.
  - The `terraform` check is **not required**, because its path filter means it doesn't run on every PR.

### Web skeleton
- **Pinned versions:** TypeScript **6.0.3** (not 7.x) and ESLint **9.39.5** (not 10.x). `typescript-eslint` 8.71 supports TypeScript `<6.1` only, and `@eslint/js` has no 10.x release that is at least two weeks old. Revisit when typescript-eslint supports TypeScript 7.
- **ESLint config:** `strictTypeChecked`, with `--max-warnings 0`. Verified that a planted `any` fails lint.
- **Playwright** is added in W5 with the review flows, not now. There is nothing to drive end-to-end yet.
- **UI language:** German, matching the client's AP team. Code and docs stay in English.
- **Repo settings:** auto-merge is enabled at the owner's request. Auto-merge still waits for the required checks.

## 2026-10-19: Session 2 (W2)
- **KoSIT runs as a plain Java daemon** (`scripts/kosit.sh`) locally and in CI. Docker isn't needed. Artefacts are SHA-256-pinned, and the checksums match GitHub's published digests. The owner approved downloading them.
- **ADR-0002 revised.** The rule set (XRechnung vs plain EN 16931) is selected by KoSIT's own scenarios through BT-24, instead of downgrading BR-DE-* in our code.
- **ZUGFeRD extraction.** pypdf extracts the attachment, and the hardened parser checks it *before* factur-x parses it, because factur-x uses lxml's default parser. factur-x is used for profile detection, the profile XSD and embedding.
- **MINIMUM / BASIC WL** profiles get `invoice=None` and `is_e_invoice=False`, so the pipeline (W3) routes them to review. This depends on the still-⚠️ BMF claim in `standards.md`.
- **Two-week rule enforced mechanically** with `exclude-newer` in `pyproject.toml`. The re-lock showed that pytest and mypy transitive dependencies in `main` were newer than allowed, so the rule had been broken without anyone noticing until now.
- **Local Windows quirks (dev machine only, CI unaffected):**
  - Windows Application Control blocks unsigned venv launchers (`mypy.exe`, `pytest.exe`). Run them as `python -m` instead; the Makefile does this.
  - It also blocks some freshly installed native DLLs on first load. Most clear on retry. `ast-serialize` 0.12.1 stays blocked, so run `mypy --no-native-parser` locally.
  - The security policy was not changed.
- **Known limit:** generated hybrid PDFs are not verified for PDF/A-3 conformance. Only the Factur-X XML is validated, by the factur-x XSD and KoSIT.

- **Dataset labels are verified, not assumed.** The first draft had 21 label/validator disagreements. They showed:
  - EN 16931 VAT rules tolerate ±1.00.
  - BR-CO-25 is only monitored, not enforced.
  - Changing BT-106 also fires BR-CO-13.
  - A missing seller VAT ID fires BR-CO-26 (+ BR-DE-16 under XRechnung).
  Labels now equal KoSIT's output exactly, and the generator refuses VAT deltas inside the tolerance.
- **A missing due date routes to review as our own rule,** because the official validator accepts it. This is documented in `standards.md`.
- **New `po_mismatch` category** (price or quantity above the PO), so 2-way matching has negative cases. It wasn't in the original brief's list, which said "at least" those five.
- **Synthetic identifiers:** VAT IDs have a deliberately invalid check digit. IBANs have valid checksums (BR-DE-19) with an all-zero bank code.
- **ZUGFeRD label hash is over the embedded XML,** because factur-x stamps PDF metadata with the current time. The committed manifest is byte-reproducible.

### Open / next session
- **W1 status:** complete. The pipeline runner skeleton follows in W3.
- **W2:** XRechnung UBL/CII and ZUGFeRD parsers, KoSIT daemon in docker compose, synthetic generator (≥ 100 labelled invoices).
- **Verify the ⚠️ legal claims** in `standards.md` against primary sources.
