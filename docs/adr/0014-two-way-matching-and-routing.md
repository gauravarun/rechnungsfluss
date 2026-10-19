# ADR-0014: Two-way PO matching, tolerances and routing
Status: **proposed** · 2026-10-19. The tolerance defaults await the project owner's confirmation; the mechanism is accepted.

## Context
The owner chose 2-way matching (invoice vs. purchase order, no goods receipt) with per-tenant tolerances. EN 16931 has no matching rules, so **every rule in this ADR is our own business logic**. It is labelled as such in code and reports (reason codes, not rule IDs).

## Decision

### Line pairing
1. If an invoice line carries **BT-132** (referenced PO line), it is paired with that PO line.
2. Otherwise it is paired by normalized description + unit code. Real supplier descriptions vary, so in practice this fallback will often fail and send the invoice to review. That is the safe direction.

### Checks and reason codes
| Check | Reason code |
|---|---|
| No BT-13 on the invoice | `no_po_reference` |
| BT-13 not found for this tenant | `po_not_found` |
| Currency differs from PO | `po_currency_mismatch` |
| Seller VAT ID present and differs from the PO supplier; if absent, the normalized names differ | `po_supplier_mismatch` |
| An invoice line pairs with no PO line | `po_line_not_found` |
| Unit code differs | `po_unit_mismatch` |
| Net price above PO price by more than the tolerance | `po_price_mismatch` |
| Quantity above PO quantity by more than the tolerance | `po_quantity_mismatch` |

- Prices **below** the PO price and quantities **below** the PO quantity are accepted. They favour the buyer, or they are partial deliveries.
- An invoice is **auto-approved only if there are no reasons at all.** Reasons from validation, duplicates, profile and due date are combined with the match reasons.

### Proposed per-tenant defaults (`domain/tenant_config.py`)
| Tenant | Price tolerance | Quantity tolerance | Duplicate window |
|---|---|---|---|
| `nordwerk-de` | +2 % or +0.02, whichever is larger | 0 % | ±3 days, gross equal within 0.01 |
| `nordwerk-at` | +2 % or +0.02, whichever is larger | 0 % | ±3 days, gross equal within 0.01 |

These are **placeholders chosen by the engineer**, not values from a real AP policy. They are deliberately strict, so a wrong value causes extra reviews rather than wrong payments. The seeded PO mismatches in the eval set (5–15 % price, 2× quantity) sit well outside them, so the eval doesn't depend on the exact values.

## Alternatives
- **Header-only match (total vs. PO total).** Simpler, but it hides line-level price creep.
- **3-way match.** Out of scope (owner decision).
- **Cumulative consumption** (subtract earlier invoices from PO quantities). Rejected for v1: it needs PO-line state across invoices, and the main over-billing risk, re-billing the same invoice, is covered by duplicate detection (ADR-0008).

## Consequences
- ➕ Every review reason is explainable to a clerk in one sentence.
- ➖ v1 doesn't detect over-billing through several partial invoices that each stay within the PO. This is documented as a known limit.
- ➖ The description fallback is brittle. W4 extraction should populate BT-132 where the PDF shows it.
