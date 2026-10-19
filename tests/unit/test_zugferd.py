import io
from pathlib import Path

import facturx
import pytest
from hypothesis import given, settings
from pypdf import PdfReader, PdfWriter

from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.formats import SPEC_EN16931, cii, zugferd
from rechnungsfluss.formats.common import ParseError
from rechnungsfluss.synthetic.render import render_pdf
from tests.strategies import invoices

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _attach(pdf: bytes, xml: bytes, level: str) -> bytes:
    """Embed without factur-x's own XSD check, to build deliberately bad inputs."""
    out: bytes = facturx.generate_from_binary(
        pdf, xml, flavor="factur-x", level=level, check_xsd=False
    )
    return out


def _raw_attach(pdf: bytes, name: str, payload: bytes) -> bytes:
    """Attach arbitrary bytes with pypdf (factur-x would refuse to embed malformed XML)."""
    writer = PdfWriter(clone_from=io.BytesIO(pdf))
    writer.add_attachment(name, payload)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@settings(max_examples=20, deadline=None)
@given(invoices())
def test_embed_extract_round_trip(inv: Invoice) -> None:
    doc = zugferd.extract(zugferd.embed(render_pdf(inv), inv))
    assert doc.profile is zugferd.Profile.EN16931
    assert doc.is_e_invoice
    assert doc.attachment_name == "factur-x.xml"
    assert doc.invoice is not None
    assert doc.invoice.model_dump(exclude={"specification_id"}) == inv.model_dump(
        exclude={"specification_id"}
    )
    assert doc.invoice.specification_id == SPEC_EN16931


def test_render_is_deterministic(invoice: Invoice) -> None:
    assert render_pdf(invoice) == render_pdf(invoice)


def test_render_marks_document_as_synthetic(invoice: Invoice) -> None:
    import io

    text = PdfReader(io.BytesIO(render_pdf(invoice))).pages[0].extract_text()
    assert "SYNTHETISCH" in text


def test_plain_pdf_is_not_zugferd(invoice: Invoice) -> None:
    with pytest.raises(zugferd.NotZugferdError):
        zugferd.extract(render_pdf(invoice))


def test_minimum_profile_is_flagged_not_an_e_invoice(invoice: Invoice) -> None:
    xml = (FIXTURES / "facturx_minimum.xml").read_bytes()
    doc = zugferd.extract(_attach(render_pdf(invoice), xml, "minimum"))
    assert doc.profile is zugferd.Profile.MINIMUM
    assert not doc.is_e_invoice
    assert doc.invoice is None


def test_xsd_failure_is_a_parse_error(invoice: Invoice) -> None:
    # A full EN 16931 invoice labelled MINIMUM violates the MINIMUM XSD.
    xml = cii.to_cii(invoice, specification_id="urn:factur-x.eu:1p0:minimum")
    with pytest.raises(ParseError, match="XSD check failed"):
        zugferd.extract(_attach(render_pdf(invoice), xml, "minimum"))


def test_embedded_doctype_is_rejected_before_factur_x_parses_it(invoice: Invoice) -> None:
    xml = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/hosts">]><x>&e;</x>'
    with pytest.raises(ParseError, match="DOCTYPE/ENTITY"):
        zugferd.extract(_raw_attach(render_pdf(invoice), "factur-x.xml", xml))


def test_unreadable_pdf() -> None:
    with pytest.raises(ParseError, match="unreadable PDF"):
        zugferd.extract(b"%PDF-1.7 garbage")


def test_oversized_pdf_is_refused() -> None:
    with pytest.raises(ParseError, match="larger than"):
        zugferd.extract(b"0" * (zugferd.MAX_PDF_BYTES + 1))
