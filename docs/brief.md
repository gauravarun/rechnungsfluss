# Project brief: Rechnungsfluss

> Portfolio project. The client, the people and all data are **fictional or synthetic**.

## Client
**Nordwerk Industriebedarf GmbH** is a fictional industrial-supplies distributor with about 400 employees. Its two legal entities are modelled as two strictly separated tenants:

| Tenant ID | Entity | Notes |
|---|---|---|
| `nordwerk-de` | Nordwerk GmbH (DE) | German entity, receives XRechnung / ZUGFeRD / PDF |
| `nordwerk-at` | Nordwerk Handels GmbH (AT) | Austrian subsidiary, processed by the same shared-service AP team |

## Problem
German businesses have had to be able to receive structured e-invoices (EN 16931) since 1 January 2025. See [standards.md](standards.md) for sources. Nordwerk still receives a mix of formats:
- XRechnung XML (UBL and CII)
- ZUGFeRD / Factur-X hybrid PDFs
- plain PDFs from small suppliers

AP clerks key data by hand, check it against purchase orders by eye, and find duplicates late.

## Users
| User | Need |
|---|---|
| AP clerk | See only the invoices that need attention. Correct, approve or reject them quickly. |
| AP team lead | Queue health, exception rate, throughput. |
| Platform operator | Alerts, dead-letter queues, safe replay. |
| Auditor | Who changed which field, when, why, and from what value. |

## What v1 does
1. Ingests PDF, XRechnung (UBL and CII) and ZUGFeRD / Factur-X.
2. Extracts data into one canonical EN 16931 model. Structured formats are parsed. Plain PDFs are extracted by a Claude model on Amazon Bedrock.
3. Validates with the official tooling (KoSIT validator, EN 16931 schematron, `factur-x`).
4. Detects duplicates.
5. Performs a 2-way match against purchase orders, with per-tenant tolerances.
6. Auto-approves clean invoices and routes exceptions to a review UI.
7. Exports approved invoices to a mock ERP API.
8. Keeps an append-only audit log of every field change.

## Success metrics
All metrics are measured on the synthetic evaluation set only. No numbers are claimed until they have been measured; see the [README](../README.md).

| Metric | Definition |
|---|---|
| Field-level accuracy | Correct fields / labelled fields, per field and per input format |
| False-accept rate | Seeded-defect invoices that were auto-approved. **Target: 0.** |
| Duplicate detection | Precision and recall on seeded duplicates |
| Review rate | Share of invoices routed to human review |
| Latency | p95 time from ingest to decision, per format |
| Cost | Estimated cost per 1,000 invoices, from measured token counts |
| Tenant isolation | Cross-tenant access attempts in tests that are denied. **Target: 100%.** |

## Out of scope (v1)
- Issuing or sending invoices.
- Peppol access point.
- Payments.
- 3-way match and goods receipts.
- Real ERP connectors.
- OCR engines other than the Claude model.
- Archiving with legal retention guarantees. GoBD is *referenced* for design inspiration only; no compliance is claimed.
- Production SSO.
- Mobile UI.
- Languages other than German and English.
