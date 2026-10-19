# ADR-0002: One canonical EN 16931 model; LLM output validated as CII
Status: accepted · 2026-10-18 · revised 2026-10-19 (point 3)

## Context
Three input formats have to produce one comparable, validated result. The brief forbids inventing validation rules.

## Decision
1. All inputs are normalized into one canonical model whose fields map to EN 16931 business terms (BT-/BG- IDs).
2. Data extracted by the LLM from plain PDFs is **serialized to UN/CEFACT CII** and validated by the same KoSIT validator as native XML. Totals and VAT consistency are therefore checked by the standard's own rules (for example BR-CO-*), not by our code.
3. **Which rule set applies is decided by the official validator, not by us.** The KoSIT XRechnung configuration contains both an "EN16931 XRechnung (CII)" scenario and a plain "EN16931 (CII)" scenario, and selects between them from the document's BT-24 specification identifier.
   - XRechnung inputs keep their XRechnung BT-24 and get the CIUS rules (BR-DE-*).
   - ZUGFeRD EN 16931 and LLM-extracted PDFs are serialized with BT-24 `urn:cen.eu:en16931:2017` and get core EN 16931 rules only, because B2B suppliers aren't bound by the XRechnung CIUS.
   - *Revision note:* the original plan downgraded BR-DE-* errors to warnings in our code. That was dropped, because using the validator's own scenarios means we don't reinterpret rule severities ourselves.

## Alternatives
- **Hand-written validation in Python.** Simple, but it invents rules and drifts from the standard.
- **A separate model per format.** Less mapping work up front, but matching, review and export would need per-format logic.

## Consequences
- ➕ A single code path after normalization. Review UI and export see one shape.
- ➕ Validation results cite official rule IDs, which is auditable.
- ➖ The CII serializer must be correct. A wrong serializer would cause false validation failures. Mitigation: round-trip property tests (`parse(serialize(x)) == x`), and the generator's clean invoices must pass KoSIT.
- ➖ Only a subset of EN 16931 is modelled (see `docs/architecture.md`). Fields outside the subset are kept as raw XML but not reviewed.
