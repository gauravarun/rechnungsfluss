"""Build arithmetically consistent synthetic invoices.

Totals are derived the way EN 16931 checks them (BR-CO-10 line sum, BR-CO-13
tax basis, BR-CO-15 grand total, BR-CO-17 VAT per category), so clean
generated invoices pass the official validator. Defects are injected
afterwards by ``synthetic.defects`` and never here.
"""

from __future__ import annotations

import datetime as dt
from collections import OrderedDict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from rechnungsfluss.domain.invoice import Invoice, InvoiceLine, Party, Totals, VatBreakdown

CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class LineSpec:
    description: str
    quantity: Decimal
    unit_code: str
    net_price: Decimal
    vat_category: str = "S"
    vat_rate: Decimal | None = Decimal("19")
    order_line_ref: str | None = None
    """BT-132: PO line number this invoice line bills."""


def build_invoice(
    *,
    number: str,
    issue_date: dt.date,
    seller: Party,
    buyer: Party,
    lines: list[LineSpec],
    currency: str = "EUR",
    due_days: int | None = 30,
    buyer_reference: str | None = None,
    purchase_order_ref: str | None = None,
    seller_iban: str | None = None,
    specification_id: str | None = None,
) -> Invoice:
    if not lines:
        raise ValueError("an invoice needs at least one line")
    invoice_lines = []
    groups: OrderedDict[tuple[str, Decimal | None], Decimal] = OrderedDict()
    for i, spec in enumerate(lines, start=1):
        net = money(spec.quantity * spec.net_price)
        invoice_lines.append(
            InvoiceLine(
                line_id=str(i),
                quantity=spec.quantity,
                unit_code=spec.unit_code,
                net_price=spec.net_price,
                net_amount=net,
                vat_category=spec.vat_category,
                vat_rate=spec.vat_rate,
                description=spec.description,
                order_line_ref=spec.order_line_ref,
            )
        )
        key = (spec.vat_category, spec.vat_rate)
        groups[key] = groups.get(key, Decimal("0")) + net

    breakdown = [
        VatBreakdown(
            taxable_amount=money(basis),
            tax_amount=money(basis * (rate or Decimal("0")) / 100),
            category=category,
            rate=rate,
        )
        for (category, rate), basis in groups.items()
    ]
    line_sum = money(sum((ln.net_amount for ln in invoice_lines), Decimal("0")))
    vat_total = money(sum((b.tax_amount for b in breakdown), Decimal("0")))
    grand = line_sum + vat_total
    return Invoice(
        number=number,
        issue_date=issue_date,
        currency=currency,
        due_date=issue_date + dt.timedelta(days=due_days) if due_days is not None else None,
        buyer_reference=buyer_reference,
        purchase_order_ref=purchase_order_ref,
        seller=seller,
        seller_iban=seller_iban,
        buyer=buyer,
        lines=tuple(invoice_lines),
        vat_breakdown=tuple(breakdown),
        totals=Totals(
            line_net_sum=line_sum,
            total_without_vat=line_sum,
            vat_total=vat_total,
            total_with_vat=grand,
            amount_due=grand,
        ),
        specification_id=specification_id,
    )
