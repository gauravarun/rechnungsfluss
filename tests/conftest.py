"""Shared fixtures. All data here is synthetic."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from rechnungsfluss.domain.invoice import (
    Contact,
    Invoice,
    InvoiceLine,
    Party,
    PostalAddress,
    Totals,
    VatBreakdown,
)
from rechnungsfluss.domain.tenancy import TenantId

# DE89370400440532013000 is the widely published example IBAN, not a real account.
EXAMPLE_IBAN = "DE89370400440532013000"


@pytest.fixture
def tenant_de() -> TenantId:
    return TenantId("nordwerk-de")


@pytest.fixture
def tenant_at() -> TenantId:
    return TenantId("nordwerk-at")


def make_invoice(**overrides: object) -> Invoice:
    data: dict[str, object] = {
        "number": "RE-2026-0001",
        "issue_date": dt.date(2026, 10, 1),
        "currency": "EUR",
        "due_date": dt.date(2026, 10, 31),
        "purchase_order_ref": "PO-DE-1001",
        "buyer_reference": "04011000-12345-34",
        "seller": Party(
            name="Muster Schrauben KG (synthetic)",
            vat_id="DE999999999",
            address=PostalAddress(
                line1="Beispielweg 1", city="Musterstadt", post_code="12345", country_code="DE"
            ),
            electronic_address="rechnung@muster-schrauben.example",
            contact=Contact(
                name="Erika Beispiel",
                phone="+49 30 0000000",
                email="erika@muster-schrauben.example",
            ),
        ),
        "seller_iban": EXAMPLE_IBAN,
        "buyer": Party(
            name="Nordwerk GmbH (fictional)",
            vat_id="DE888888888",
            address=PostalAddress(
                line1="Werkstraße 7", city="Nordstadt", post_code="54321", country_code="DE"
            ),
            electronic_address="ap@nordwerk.example",
        ),
        "lines": (
            InvoiceLine(
                line_id="1",
                quantity=Decimal("10"),
                unit_code="H87",
                net_price=Decimal("12.50"),
                net_amount=Decimal("125.00"),
                vat_category="S",
                vat_rate=Decimal("19"),
                description="Sechskantschraube M8",
            ),
        ),
        "vat_breakdown": (
            VatBreakdown(
                taxable_amount=Decimal("125.00"),
                tax_amount=Decimal("23.75"),
                category="S",
                rate=Decimal("19"),
            ),
        ),
        "totals": Totals(
            line_net_sum=Decimal("125.00"),
            total_without_vat=Decimal("125.00"),
            vat_total=Decimal("23.75"),
            total_with_vat=Decimal("148.75"),
            amount_due=Decimal("148.75"),
        ),
    }
    data.update(overrides)
    return Invoice.model_validate(data)


@pytest.fixture
def invoice() -> Invoice:
    return make_invoice()
