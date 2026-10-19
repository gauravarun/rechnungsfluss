"""Map the model's print-oriented output onto the canonical invoice model.

Rules (documented in ADR-0004 amendment):
- Values are parsed strictly; anything unparseable counts as missing.
- Nothing is computed to fill a gap, with one documented equivalence: when
  the document prints no separate line sum (BT-106), it is taken from the
  printed net total (BT-109). The canonical model has no allowances or charges,
  so the two are equal by definition. If the document prints both, both are
  read as printed, so a wrong line sum stays visible to the validator.
- VAT category (BT-151/BT-118) is not printed on the documents we receive:
  "S" (standard) when the rate is above 0, "Z" (zero rated) when it is 0.
- Missing required data does not raise: the result carries ``missing`` field
  names and the pipeline routes the document to review.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from rechnungsfluss.domain.invoice import FieldSource, Invoice, Provenance
from rechnungsfluss.extraction.schema import ExtractedInvoice, ExtractedParty


@dataclass
class MappingResult:
    invoice: Invoice | None
    missing: list[str] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)


class _Collector:
    def __init__(self) -> None:
        self.missing: list[str] = []

    def text(self, value: str | None, path: str, *, required: bool = True) -> str | None:
        cleaned = value.strip() if value else None
        if not cleaned and required:
            self.missing.append(path)
        return cleaned or None

    def amount(
        self, value: str | None, path: str, *, places: int = 2, required: bool = True
    ) -> Decimal | None:
        raw = self.text(value, path, required=required)
        if raw is None:
            return None
        try:
            number = Decimal(raw)
        except InvalidOperation:
            self.missing.append(path)
            return None
        if not number.is_finite() or number.as_tuple().exponent < -places:  # type: ignore[operator]
            self.missing.append(path)  # more precision than the model allows: misread
            return None
        return number

    def date(self, value: str | None, path: str, *, required: bool = True) -> dt.date | None:
        raw = self.text(value, path, required=required)
        if raw is None:
            return None
        try:
            return dt.date.fromisoformat(raw)
        except ValueError:
            self.missing.append(path)
            return None


def _party(c: _Collector, p: ExtractedParty, prefix: str) -> dict[str, Any]:
    return {
        "name": c.text(p.name, f"{prefix}.name"),
        "vat_id": c.text(p.vat_id, f"{prefix}.vat_id", required=False),
        "address": {
            "line1": c.text(p.address.line1, f"{prefix}.address.line1", required=False),
            "city": c.text(p.address.city, f"{prefix}.address.city", required=False),
            "post_code": c.text(p.address.post_code, f"{prefix}.address.post_code", required=False),
            "country_code": c.text(p.address.country_code, f"{prefix}.address.country_code"),
        },
        "electronic_address": c.text(p.email, f"{prefix}.electronic_address", required=False),
    }


def to_canonical(x: ExtractedInvoice) -> MappingResult:
    c = _Collector()
    lines = []
    for i, line in enumerate(x.lines):
        p = f"lines[{i}]"
        rate = c.amount(line.vat_rate_percent, f"{p}.vat_rate")
        lines.append(
            {
                "line_id": c.text(line.position, f"{p}.line_id", required=False) or str(i + 1),
                "quantity": c.amount(line.quantity, f"{p}.quantity", places=4),
                "unit_code": c.text(line.unit_code, f"{p}.unit_code"),
                "net_price": c.amount(line.net_price, f"{p}.net_price"),
                "net_amount": c.amount(line.net_amount, f"{p}.net_amount"),
                "vat_category": "Z" if rate == 0 else "S",
                "vat_rate": rate,
                "description": c.text(line.description, f"{p}.description", required=False),
                "order_line_ref": c.text(
                    line.order_line_ref, f"{p}.order_line_ref", required=False
                ),
            }
        )
    if not lines:
        c.missing.append("lines")
    breakdown = []
    for i, vat in enumerate(x.vat_breakdown):
        p = f"vat_breakdown[{i}]"
        rate = c.amount(vat.rate_percent, f"{p}.rate")
        breakdown.append(
            {
                "taxable_amount": c.amount(vat.taxable_amount, f"{p}.taxable_amount"),
                "tax_amount": c.amount(vat.tax_amount, f"{p}.tax_amount"),
                "category": "Z" if rate == 0 else "S",
                "rate": rate,
            }
        )
    if not breakdown:
        c.missing.append("vat_breakdown")
    net_total = c.amount(x.totals.total_without_vat, "totals.total_without_vat")
    line_sum = c.amount(x.totals.line_net_sum, "totals.line_net_sum", required=False)
    contact = x.seller_contact
    seller = _party(c, x.seller, "seller")
    if contact is not None:
        seller["contact"] = {
            "name": c.text(contact.name, "seller.contact.name", required=False),
            "phone": c.text(contact.phone, "seller.contact.phone", required=False),
            "email": c.text(contact.email, "seller.contact.email", required=False),
        }
    data: dict[str, Any] = {
        "number": c.text(x.number, "number"),
        "issue_date": c.date(x.issue_date, "issue_date"),
        "currency": c.text(x.currency, "currency"),
        "due_date": c.date(x.due_date, "due_date", required=False),
        "buyer_reference": c.text(x.buyer_reference, "buyer_reference", required=False),
        "purchase_order_ref": c.text(x.purchase_order_ref, "purchase_order_ref", required=False),
        "seller": seller,
        "seller_iban": c.text(x.seller_iban, "seller_iban", required=False),
        "buyer": _party(c, x.buyer, "buyer"),
        "lines": lines,
        "vat_breakdown": breakdown,
        "totals": {
            "line_net_sum": line_sum if line_sum is not None else net_total,
            "total_without_vat": net_total,
            "vat_total": c.amount(x.totals.vat_total, "totals.vat_total"),
            "total_with_vat": c.amount(x.totals.total_with_vat, "totals.total_with_vat"),
            "amount_due": c.amount(x.totals.amount_due, "totals.amount_due"),
        },
    }
    uncertain = sorted(set(x.uncertain_fields))
    if c.missing:
        return MappingResult(invoice=None, missing=sorted(set(c.missing)), uncertain=uncertain)
    try:
        invoice = Invoice.model_validate(data)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        return MappingResult(invoice=None, missing=fields, uncertain=uncertain)
    provenance = {path: Provenance(source=FieldSource.LLM) for path in invoice.field_paths()}
    return MappingResult(
        invoice=invoice.model_copy(update={"provenance": provenance}), uncertain=uncertain
    )
