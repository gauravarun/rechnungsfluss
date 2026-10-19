import pytest
from hypothesis import given, settings

from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.formats import SPEC_EN16931, SPEC_XRECHNUNG_3_0, cii, ubl
from rechnungsfluss.formats.common import ParseError
from tests.strategies import invoices


def _without_spec(inv: Invoice) -> dict[str, object]:
    return inv.model_dump(exclude={"specification_id"})


@settings(max_examples=150, deadline=None)
@given(invoices())
def test_round_trip_preserves_every_modelled_field(inv: Invoice) -> None:
    parsed = ubl.from_ubl(ubl.to_ubl(inv, specification_id=SPEC_XRECHNUNG_3_0))
    assert _without_spec(parsed) == _without_spec(inv)
    assert parsed.specification_id == SPEC_XRECHNUNG_3_0


@settings(max_examples=50, deadline=None)
@given(invoices())
def test_ubl_and_cii_parse_to_the_same_canonical_invoice(inv: Invoice) -> None:
    via_ubl = ubl.from_ubl(ubl.to_ubl(inv, specification_id=SPEC_EN16931))
    via_cii = cii.from_cii(cii.to_cii(inv, specification_id=SPEC_EN16931))
    assert via_ubl == via_cii


def test_vat_id_ignores_non_vat_tax_schemes(invoice: Invoice) -> None:
    xml = ubl.to_ubl(invoice, specification_id=SPEC_EN16931)
    # Insert a German tax number (scheme FC, BT-32) before the VAT registration.
    fc = (
        b"<cac:PartyTaxScheme><cbc:CompanyID>201/113/40209</cbc:CompanyID>"
        b"<cac:TaxScheme><cbc:ID>FC</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>"
    )
    xml = xml.replace(b"<cac:PartyTaxScheme>", fc + b"<cac:PartyTaxScheme>", 1)
    assert ubl.from_ubl(xml).seller.vat_id == "DE999999999"


def test_rejects_cii_document() -> None:
    with pytest.raises(ParseError, match="not a UBL invoice"):
        ubl.from_ubl(b"<x/>")


def test_missing_issue_date(invoice: Invoice) -> None:
    xml = ubl.to_ubl(invoice, specification_id=SPEC_EN16931)
    xml = xml.replace(b"<cbc:IssueDate>2026-10-01</cbc:IssueDate>", b"")
    with pytest.raises(ParseError, match="IssueDate"):
        ubl.from_ubl(xml)


def test_bad_date(invoice: Invoice) -> None:
    xml = ubl.to_ubl(invoice, specification_id=SPEC_EN16931)
    xml = xml.replace(b"2026-10-01</cbc:IssueDate>", b"01.10.2026</cbc:IssueDate>")
    with pytest.raises(ParseError, match="bad date"):
        ubl.from_ubl(xml)
