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

### Open / next session
- **W1 remainder:** web skeleton (Vite + React + TS) with eslint/tsc in CI. The pipeline runner skeleton follows in W3.
- **W2:** XRechnung UBL/CII and ZUGFeRD parsers, KoSIT daemon in docker compose, synthetic generator (≥ 100 labelled invoices).
- **Verify the ⚠️ legal claims** in `standards.md` against primary sources.
- **Repository hosting:** the repo is local only, with no GitHub remote yet. PRs are simulated with `--no-ff` merges.
