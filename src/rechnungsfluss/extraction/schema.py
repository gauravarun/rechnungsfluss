"""What the model returns: a flat, print-oriented view of an invoice (ADR-0004).

This is deliberately *not* the canonical model. Amounts and dates are strings
exactly as the model read them, so parsing and normalization happen in our
code (``mapping.py``) where they are tested, and a misread value fails loudly
instead of being silently coerced. Every field the model cannot read is null
and listed in ``uncertain_fields``; the prompt forbids guessing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedAddress(_Strict):
    line1: str | None
    city: str | None
    post_code: str | None
    country_code: str | None
    """ISO 3166-1 alpha-2, e.g. DE, AT."""


class ExtractedParty(_Strict):
    name: str | None
    vat_id: str | None
    address: ExtractedAddress
    email: str | None


class ExtractedContact(_Strict):
    name: str | None
    phone: str | None
    email: str | None


class ExtractedLine(_Strict):
    position: str | None
    description: str | None
    quantity: str | None
    unit_code: str | None
    net_price: str | None
    net_amount: str | None
    vat_rate_percent: str | None
    order_line_ref: str | None
    """The PO line number if the invoice prints one, else null."""


class ExtractedVat(_Strict):
    rate_percent: str | None
    taxable_amount: str | None
    tax_amount: str | None


class ExtractedTotals(_Strict):
    line_net_sum: str | None
    """Sum of the line amounts as printed ("Summe Positionen"), if printed."""
    total_without_vat: str | None
    vat_total: str | None
    total_with_vat: str | None
    amount_due: str | None


class ExtractedInvoice(_Strict):
    is_invoice: bool
    """False if the document is not an invoice (e.g. a reminder letter or delivery note)."""
    number: str | None
    issue_date: str | None
    """YYYY-MM-DD."""
    due_date: str | None
    currency: str | None
    """ISO 4217, e.g. EUR."""
    purchase_order_ref: str | None
    buyer_reference: str | None
    seller: ExtractedParty
    seller_contact: ExtractedContact | None
    seller_iban: str | None
    buyer: ExtractedParty
    lines: list[ExtractedLine]
    vat_breakdown: list[ExtractedVat]
    totals: ExtractedTotals
    uncertain_fields: list[str]
    """Field names the model could not read with confidence (null or doubtful values)."""
