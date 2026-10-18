# Standards and sources

This project does **not** invent invoice validation rules. Every rule applied to an invoice comes from one of the sources below. Our own business logic is limited to two areas, and it is labelled as ours wherever it appears:
- PO matching tolerances
- duplicate heuristics

Status legend:
- ✅ verified against the primary source (date noted)
- ⚠️ taken from secondary sources; to be verified against the primary source before W6

## Legal context (Germany)
| Claim | Source | Status |
|---|---|---|
| Since 1 Jan 2025, domestic B2B businesses must be able to receive e-invoices. An e-invoice is a structured format that complies with EN 16931. | § 14 Abs. 1 UStG as amended by the Wachstumschancengesetz (2024). Primary text: <https://www.gesetze-im-internet.de/ustg_1980/__14.html> | ⚠️ |
| Issuing obligation phases in during 2027–2028. | Same law, transitional provisions (§ 27 Abs. 38 UStG) | ⚠️ |
| ZUGFeRD profiles MINIMUM and BASIC-WL do not qualify as e-invoices. | BMF-Schreiben on e-invoicing (Oct 2024), <https://www.bundesfinanzministerium.de> | ⚠️ |

The pipeline treats a ZUGFeRD file whose profile is MINIMUM or BASIC-WL as an **exception for review**, not as a valid e-invoice. That behaviour depends on the last row above, which still needs verifying.

## Technical standards and tooling (pinned)
| Component | Version pinned | Source | Status |
|---|---|---|---|
| EN 16931-1:2017 semantic data model (BT/BG identifiers) | n/a | CEN. The BT/BG IDs are used throughout `domain/` | ✅ |
| EN 16931 validation artefacts (CII and UBL schematron) | `validation-1.3.17` (2026-10-12) | <https://github.com/ConnectingEurope/eInvoicing-EN16931/releases> | ✅ checked 2026-10-18 |
| XRechnung CIUS | 3.0.2 | KoSIT, <https://xeinkauf.de/xrechnung/> | ✅ |
| KoSIT validator | `v1.6.3` (2026-08-20) | <https://github.com/itplr-kosit/validator/releases> | ✅ checked 2026-10-18 |
| KoSIT XRechnung configuration | `v2026-08-31` (XRechnung 3.0.2) | <https://github.com/itplr-kosit/validator-configuration-xrechnung/releases> | ✅ checked 2026-10-18 |
| Factur-X / ZUGFeRD library | `factur-x` 7.3 (PyPI) | <https://github.com/akretion/factur-x> | ✅ checked 2026-10-18 |

The **EN 16931 schematron** is still pinned in case the project later needs core-only validation. It is not used yet: the KoSIT XRechnung configuration already includes the EN 16931 core rules.

## Which rules apply to which input
| Input | Rules applied |
|---|---|
| XRechnung (UBL or CII) | KoSIT validator + XRechnung configuration (EN 16931 core + XRechnung CIUS rules, BR-DE-*) |
| ZUGFeRD / Factur-X | `factur-x`: XML extraction + XSD for the declared profile. Then the KoSIT validator on the extracted CII. XRechnung-only (BR-DE-*) findings are reported as **warnings**, not errors, because B2B suppliers aren't bound by the XRechnung CIUS. See ADR-0002. |
| Plain PDF (LLM-extracted) | Extracted data is serialized to CII and run through the same KoSIT validation as ZUGFeRD |

Pin updates are recorded in [DECISIONS.md](DECISIONS.md).
