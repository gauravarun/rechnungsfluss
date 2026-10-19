"""Two-way matching: invoice vs. purchase order (ADR-0014). Our own business rules."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from rechnungsfluss.domain.invoice import Invoice, InvoiceLine
from rechnungsfluss.domain.reasons import Reason
from rechnungsfluss.domain.tenant_config import MatchTolerances


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PurchaseOrderLine(_Frozen):
    line_no: int
    description: str
    quantity: Decimal
    unit_code: str
    net_price: Decimal


class PurchaseOrder(_Frozen):
    tenant: str
    number: str
    supplier_vat_id: str | None
    supplier_name: str
    currency: str
    lines: Annotated[tuple[PurchaseOrderLine, ...], Field(min_length=1)]


class MatchIssue(_Frozen):
    reason: Reason
    detail: str
    invoice_line: str | None = None


_SPACE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    return _SPACE.sub(" ", value).strip().casefold()


def _pair(line: InvoiceLine, po: PurchaseOrder) -> PurchaseOrderLine | None:
    if line.order_line_ref is not None:
        try:
            wanted = int(line.order_line_ref)
        except ValueError:
            return None
        return next((p for p in po.lines if p.line_no == wanted), None)
    desc = normalize_text(line.description or "")
    return next(
        (
            p
            for p in po.lines
            if normalize_text(p.description) == desc and p.unit_code == line.unit_code
        ),
        None,
    )


def _price_limit(po_price: Decimal, tol: MatchTolerances) -> Decimal:
    return po_price + max(po_price * tol.price_pct / 100, tol.price_abs)


def match(invoice: Invoice, po: PurchaseOrder | None, tol: MatchTolerances) -> list[MatchIssue]:
    """Return every match issue; an empty list means the invoice matches its PO."""
    if invoice.purchase_order_ref is None:
        return [MatchIssue(reason=Reason.NO_PO_REFERENCE, detail="invoice has no BT-13")]
    if po is None:
        return [
            MatchIssue(
                reason=Reason.PO_NOT_FOUND, detail=f"PO {invoice.purchase_order_ref} not found"
            )
        ]

    issues: list[MatchIssue] = []
    if invoice.currency != po.currency:
        issues.append(
            MatchIssue(
                reason=Reason.PO_CURRENCY_MISMATCH, detail=f"{invoice.currency} vs PO {po.currency}"
            )
        )
    seller = invoice.seller
    if seller.vat_id is not None and po.supplier_vat_id is not None:
        supplier_ok = seller.vat_id == po.supplier_vat_id
    else:
        supplier_ok = normalize_text(seller.name) == normalize_text(po.supplier_name)
    if not supplier_ok:
        issues.append(
            MatchIssue(
                reason=Reason.PO_SUPPLIER_MISMATCH, detail="seller does not match the PO supplier"
            )
        )

    for line in invoice.lines:
        po_line = _pair(line, po)
        if po_line is None:
            issues.append(
                MatchIssue(
                    reason=Reason.PO_LINE_NOT_FOUND,
                    invoice_line=line.line_id,
                    detail="no matching PO line",
                )
            )
            continue
        if po_line.unit_code != line.unit_code:
            issues.append(
                MatchIssue(
                    reason=Reason.PO_UNIT_MISMATCH,
                    invoice_line=line.line_id,
                    detail=f"{line.unit_code} vs PO {po_line.unit_code}",
                )
            )
            continue
        if line.net_price > _price_limit(po_line.net_price, tol):
            issues.append(
                MatchIssue(
                    reason=Reason.PO_PRICE_MISMATCH,
                    invoice_line=line.line_id,
                    detail=f"{line.net_price} vs PO {po_line.net_price}",
                )
            )
        if line.quantity > po_line.quantity * (1 + tol.quantity_pct / 100):
            issues.append(
                MatchIssue(
                    reason=Reason.PO_QUANTITY_MISMATCH,
                    invoice_line=line.line_id,
                    detail=f"{line.quantity} vs PO {po_line.quantity}",
                )
            )
    return issues
