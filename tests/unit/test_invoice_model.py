from decimal import Decimal

import pytest
from pydantic import ValidationError

from rechnungsfluss.domain.invoice import FieldSource, Invoice, Provenance
from tests.conftest import make_invoice


def test_valid_invoice_builds(invoice: Invoice) -> None:
    assert invoice.totals.amount_due == Decimal("148.75")
    assert invoice.type_code == "380"


def test_model_is_immutable(invoice: Invoice) -> None:
    with pytest.raises(ValidationError):
        invoice.number = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("currency", "eur"),
        ("currency", "EURO"),
        ("seller_iban", "not-an-iban"),
        ("type_code", "38"),
        ("number", ""),
        ("lines", ()),
        ("vat_breakdown", ()),
    ],
)
def test_structural_validation_rejects(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_invoice(**{field: value})


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        make_invoice(surprise="x")


def test_amounts_reject_more_than_two_decimals(invoice: Invoice) -> None:
    data = invoice.model_dump()
    data["totals"]["amount_due"] = Decimal("148.755")
    with pytest.raises(ValidationError):
        Invoice.model_validate(data)


def test_model_does_not_enforce_business_rules() -> None:
    """Totals consistency is the validator's job (ADR-0002), not the model's."""
    inv = make_invoice()
    data = inv.model_dump()
    data["totals"]["total_with_vat"] = Decimal("999.99")
    assert Invoice.model_validate(data).totals.total_with_vat == Decimal("999.99")


def test_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        Provenance(source=FieldSource.LLM, confidence=1.5)


def test_field_paths_flattens_nested_values(invoice: Invoice) -> None:
    paths = invoice.field_paths()
    assert paths["totals.amount_due"] == Decimal("148.75")
    assert paths["lines[0].quantity"] == Decimal("10")
    assert paths["seller.vat_id"] == "DE999999999"
    assert not any(p.startswith("provenance") for p in paths)
