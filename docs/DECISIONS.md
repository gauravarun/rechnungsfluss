# Decisions log

Short, dated entries per work session. Significant architectural decisions get a full ADR in [adr/](adr/).

## 2026-10-18: Session 1 (planning + W1 start)
- **Plan approved** by the project owner: brief, architecture, 6-week milestones, 13 ADRs.
- **Owner choices:**
  - Python 3.12 + FastAPI on AWS.
  - 2-way PO match with per-tenant tolerances (no goods receipts in v1).
  - Langfuse Cloud EU.
  - Recorded-eval on PRs + live-eval nightly.
  - The accuracy threshold will be set by the owner after the W4 baseline; until then the gate is regression-only (−2 pp).
- **Changed from plan: LocalStack dropped** → in-process local mode + moto (ADR-0009). Reason: upstream repo archived 2026-03-23, and an auth token is required since 2026.03.0 (verified via GitHub API and LocalStack release notes).
- **Validation versions pinned** (verified on GitHub 2026-10-18): KoSIT validator v1.6.3, XRechnung configuration v2026-08-31 (XRechnung 3.0.2), EN 16931 artefacts validation-1.3.17.
- **ZUGFeRD/PDF inputs:** XRechnung BR-DE-* findings are downgraded to warnings (ADR-0002).
- **Fictional client:** Nordwerk Industriebedarf GmbH. Tenants `nordwerk-de` and `nordwerk-at`.
