# ADR-0002: One canonical EN 16931 model; LLM output validated as CII
Status: accepted · 2026-10-18

## Context
Three input formats have to produce one comparable, validated result. The brief forbids inventing validation rules.

## Decision
1. All inputs are normalized into one canonical model whose fields map to EN 16931 business terms (BT-/BG- IDs).
2. Data extracted by the LLM from plain PDFs is **serialized to UN/CEFACT CII** and validated by the same KoSIT validator as native XML. Totals and VAT consistency are therefore checked by the standard's own rules (for example BR-CO-*), not by our code.
3. XRechnung-specific CIUS rules (BR-DE-*) are **errors** for XRechnung inputs and **warnings** for ZUGFeRD and PDF inputs. B2B suppliers are not bound by the XRechnung CIUS.

## Alternatives
- **Hand-written validation in Python.** Simple, but it invents rules and drifts from the standard.
- **A separate model per format.** Less mapping work up front, but matching, review and export would need per-format logic.

## Consequences
- ➕ A single code path after normalization. Review UI and export see one shape.
- ➕ Validation results cite official rule IDs, which is auditable.
- ➖ The CII serializer must be correct. A wrong serializer would cause false validation failures. Mitigation: round-trip property tests (`parse(serialize(x)) == x`), and the generator's clean invoices must pass KoSIT.
- ➖ Only a subset of EN 16931 is modelled (see `docs/architecture.md`). Fields outside the subset are kept as raw XML but not reviewed.
