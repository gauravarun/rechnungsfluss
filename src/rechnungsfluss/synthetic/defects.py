"""Seeded defects for the evaluation set.

Each defect names the official rule IDs the KoSIT validator is *expected* to
report. These expectations are not taken on trust: they are asserted against
the real validator in tests/integration/test_dataset_kosit.py. Defects that
no EN 16931 rule covers (missing PO, PO mismatch, duplicates) are labelled
with *our* review reasons instead (see docs/adr/0008 and the W3 matching ADR).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from rechnungsfluss.domain.invoice import Invoice, Party, Totals, VatBreakdown


@dataclass(frozen=True, slots=True)
class Defect:
    name: str
    invoice: Invoice
    rule_ids: tuple[str, ...]
    """Official rule IDs expected from KoSIT (empty if the document stays valid)."""
    xrechnung_rule_ids: tuple[str, ...] = ()
    """Additional IDs when the document is validated under the XRechnung CIUS."""


def _totals(inv: Invoice, **changes: Decimal) -> Totals:
    return inv.totals.model_copy(update=changes)


def wrong_grand_total(inv: Invoice, delta: Decimal) -> Defect:
    t = _totals(
        inv,
        total_with_vat=inv.totals.total_with_vat + delta,
        amount_due=inv.totals.amount_due + delta,
    )
    return Defect("wrong_grand_total", inv.model_copy(update={"totals": t}), ("BR-CO-15",))


def wrong_line_sum(inv: Invoice, delta: Decimal) -> Defect:
    """BT-106 no longer equals the sum of line net amounts (BT-131).

    BT-109 is left unchanged, so BR-CO-13 (BT-109 = BT-106 - allowances + charges) fires too.
    """
    t = _totals(inv, line_net_sum=inv.totals.line_net_sum + delta)
    return Defect("wrong_line_sum", inv.model_copy(update={"totals": t}), ("BR-CO-10", "BR-CO-13"))


def wrong_vat_amount(inv: Invoice, delta: Decimal) -> Defect:
    """First VAT breakdown amount off by ``delta``; header VAT total kept consistent with it.

    BR-CO-17 and BR-S-09 tolerate +/-1.00 (BR-CO-17 inclusive, BR-S-09 exclusive, per the
    EN 16931 schematron in the pinned KoSIT configuration), so |delta| must exceed 1.00.
    """
    if abs(delta) <= 1:
        raise ValueError("VAT deltas within +/-1.00 are inside the official rounding tolerance")
    first = inv.vat_breakdown[0]
    changed = VatBreakdown.model_validate(
        {**first.model_dump(), "tax_amount": first.tax_amount + delta}
    )
    breakdown = (changed, *inv.vat_breakdown[1:])
    t = _totals(
        inv,
        vat_total=inv.totals.vat_total + delta,
        total_with_vat=inv.totals.total_with_vat + delta,
        amount_due=inv.totals.amount_due + delta,
    )
    return Defect(
        "wrong_vat_amount",
        inv.model_copy(update={"vat_breakdown": breakdown, "totals": t}),
        ("BR-CO-17", "BR-S-09"),
    )


def missing_seller_vat_id(inv: Invoice) -> Defect:
    seller = Party.model_validate({**inv.seller.model_dump(), "vat_id": None})
    return Defect(
        "missing_seller_vat_id",
        inv.model_copy(update={"seller": seller}),
        ("BR-CO-26", "BR-S-02"),
        xrechnung_rule_ids=("BR-DE-16",),
    )


def missing_due_date(inv: Invoice) -> Defect:
    """No BT-9 and no payment terms (BT-20).

    EN 16931 BR-CO-25 would cover this, but the pinned KoSIT configuration only
    *monitors* BR-CO-25 (see its CHANGELOG), so the official validator accepts the
    document. Routing it to review is our own rule (reason ``missing_due_date``).
    """
    return Defect("missing_due_date", inv.model_copy(update={"due_date": None}), ())


def missing_buyer_reference(inv: Invoice) -> Defect:
    """Only an error under the XRechnung CIUS."""
    return Defect(
        "missing_buyer_reference",
        inv.model_copy(update={"buyer_reference": None}),
        ("BR-DE-15",),
    )


def missing_po_reference(inv: Invoice) -> Defect:
    """Valid e-invoice, but 2-way matching has nothing to match against (our rule)."""
    return Defect("missing_po_reference", inv.model_copy(update={"purchase_order_ref": None}), ())
