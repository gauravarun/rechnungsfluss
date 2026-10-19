import pytest
from hypothesis import given, settings
from lxml import etree

from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.formats import SPEC_EN16931, SPEC_XRECHNUNG_3_0, cii
from rechnungsfluss.formats.common import ParseError
from tests.strategies import invoices


def _without_spec(inv: Invoice) -> dict[str, object]:
    return inv.model_dump(exclude={"specification_id"})


@settings(max_examples=150, deadline=None)
@given(invoices())
def test_round_trip_preserves_every_modelled_field(inv: Invoice) -> None:
    parsed = cii.from_cii(cii.to_cii(inv, specification_id=SPEC_EN16931))
    assert _without_spec(parsed) == _without_spec(inv)
    assert parsed.specification_id == SPEC_EN16931


def test_specification_id_is_written_and_read(invoice: Invoice) -> None:
    xml = cii.to_cii(invoice, specification_id=SPEC_XRECHNUNG_3_0)
    assert cii.from_cii(xml).specification_id == SPEC_XRECHNUNG_3_0


def test_output_is_utf8_with_declaration(invoice: Invoice) -> None:
    xml = cii.to_cii(invoice, specification_id=SPEC_EN16931)
    assert xml.startswith(b"<?xml version='1.0' encoding='UTF-8'?>")


@pytest.mark.parametrize(
    "payload",
    [
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>',
        b'<!DOCTYPE lolz [<!ENTITY lol "lol">]><x>&lol;</x>',
    ],
)
def test_rejects_doctype_and_entities(payload: bytes) -> None:
    with pytest.raises(ParseError, match="DOCTYPE/ENTITY"):
        cii.from_cii(payload)


def test_rejects_malformed_xml() -> None:
    with pytest.raises(ParseError, match="malformed"):
        cii.from_cii(b"<rsm:CrossIndustryInvoice")


def test_rejects_other_root() -> None:
    with pytest.raises(ParseError, match="not a CII invoice"):
        cii.from_cii(b"<Invoice/>")


def test_missing_required_element_is_a_parse_error(invoice: Invoice) -> None:
    root = etree.fromstring(cii.to_cii(invoice, specification_id=SPEC_EN16931))
    doc_id = root.find("rsm:ExchangedDocument/ram:ID", cii.NS)
    assert doc_id is not None
    doc_id.getparent().remove(doc_id)  # type: ignore[union-attr]
    with pytest.raises(ParseError, match="ram:ID"):
        cii.from_cii(etree.tostring(root))


def test_model_violations_become_parse_errors(invoice: Invoice) -> None:
    xml = cii.to_cii(invoice, specification_id=SPEC_EN16931).replace(
        b"<ram:InvoiceCurrencyCode>EUR", b"<ram:InvoiceCurrencyCode>euro"
    )
    with pytest.raises(ParseError, match="canonical model"):
        cii.from_cii(xml)
