"""Canonical invoice model based on the EN 16931 semantic data model.

Each field documents its EN 16931 business term (BT) or group (BG). Only the
subset listed in docs/architecture.md is modelled (ADR-0002). This module does
structural validation only (types, formats, required fields). Business rules
such as total consistency are checked by the official validators, not here.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Amount = Annotated[Decimal, Field(max_digits=19, decimal_places=2)]
Quantity = Annotated[Decimal, Field(max_digits=19, decimal_places=4)]
Percent = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
# EU VAT identifiers start with a 2-letter country prefix (Greece uses "EL").
VatId = Annotated[
    str, StringConstraints(pattern=r"^[A-Z]{2}[A-Z0-9+*.]{2,13}$", strip_whitespace=True)
]
Iban = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$", strip_whitespace=True),
]
NonEmpty = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InputFormat(StrEnum):
    XRECHNUNG_UBL = "xrechnung-ubl"
    XRECHNUNG_CII = "xrechnung-cii"
    ZUGFERD = "zugferd"
    PDF = "pdf"


class FieldSource(StrEnum):
    XML = "xml"
    LLM = "llm"
    HUMAN = "human"


class Provenance(_Frozen):
    """Where a field value came from. ``confidence`` is set only for LLM output."""

    source: FieldSource
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] | None = None


class Party(_Frozen):
    name: NonEmpty
    """BT-27 (seller) / BT-44 (buyer)."""
    vat_id: VatId | None = None
    """BT-31 (seller) / BT-48 (buyer) VAT identifier."""


class InvoiceLine(_Frozen):
    """BG-25 invoice line."""

    line_id: NonEmpty
    """BT-126."""
    quantity: Quantity
    """BT-129."""
    unit_code: Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{1,3}$")]
    """BT-130, UN/ECE Rec 20 code, e.g. ``H87`` (piece)."""
    net_price: Amount
    """BT-146."""
    net_amount: Amount
    """BT-131."""
    vat_category: Annotated[str, StringConstraints(pattern=r"^(S|Z|E|AE|K|G|O|L|M)$")]
    """BT-151, UNCL5305 subset allowed by EN 16931."""
    vat_rate: Percent | None = None
    """BT-152."""
    description: str | None = None
    """BT-153 item name."""


class VatBreakdown(_Frozen):
    """BG-23 VAT breakdown."""

    taxable_amount: Amount
    """BT-116."""
    tax_amount: Amount
    """BT-117."""
    category: Annotated[str, StringConstraints(pattern=r"^(S|Z|E|AE|K|G|O|L|M)$")]
    """BT-118."""
    rate: Percent | None = None
    """BT-119."""


class Totals(_Frozen):
    """BG-22 document totals."""

    line_net_sum: Amount
    """BT-106."""
    total_without_vat: Amount
    """BT-109."""
    vat_total: Amount
    """BT-110."""
    total_with_vat: Amount
    """BT-112."""
    amount_due: Amount
    """BT-115."""


class Invoice(_Frozen):
    """Canonical invoice. All inputs are normalized into this shape."""

    number: NonEmpty
    """BT-1."""
    issue_date: dt.date
    """BT-2."""
    type_code: Annotated[str, StringConstraints(pattern=r"^[0-9]{3}$")] = "380"
    """BT-3, UNTDID 1001 (380 = commercial invoice, 381 = credit note)."""
    currency: CurrencyCode
    """BT-5."""
    due_date: dt.date | None = None
    """BT-9."""
    buyer_reference: str | None = None
    """BT-10 (Leitweg-ID for public-sector XRechnung)."""
    purchase_order_ref: str | None = None
    """BT-13."""
    seller: Party
    """BG-4."""
    seller_iban: Iban | None = None
    """BT-84."""
    buyer: Party
    """BG-7."""
    lines: Annotated[tuple[InvoiceLine, ...], Field(min_length=1)]
    """BG-25 (EN 16931 requires at least one line)."""
    vat_breakdown: Annotated[tuple[VatBreakdown, ...], Field(min_length=1)]
    """BG-23."""
    totals: Totals
    """BG-22."""
    provenance: dict[str, Provenance] = Field(default_factory=dict)
    """Field path (e.g. ``totals.amount_due``) -> provenance."""

    def field_paths(self) -> dict[str, object]:
        """Flatten reviewable values to ``path -> value`` (used for diffs and eval)."""
        return _flatten(self.model_dump(mode="python", exclude={"provenance"}))


def _flatten(value: object, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(value, list | tuple):
        for i, v in enumerate(value):
            out.update(_flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = value
    return out
