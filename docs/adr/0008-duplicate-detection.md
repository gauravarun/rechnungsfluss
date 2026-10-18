# ADR-0008: Duplicates go to review, never auto-reject
Status: accepted · 2026-10-18

## Context
Suppliers re-send invoices as reminders, as a PDF after an XML, or with corrections. Paying twice is costly, and wrongly rejecting a legitimate invoice is also costly.

## Decision
This is **our own business rule**, not a standard, and it is labelled as such in code and docs.
- **Exact key:** `(tenant, seller VAT ID BT-31, invoice number BT-1)`, after normalizing case, whitespace and leading zeros.
- **Fuzzy secondary:** same seller, an absolute gross-amount difference of at most 0.01, issue date within ±3 days, and a different number.
- Any hit routes to **review** with the suspected original linked. The system never auto-rejects.

## Consequences
- ➕ No silent loss of legitimate invoices.
- ➖ The fuzzy rule causes false positives, measured as duplicate-detection precision in the eval. The thresholds are configuration, tuned against the eval set and not guessed.
