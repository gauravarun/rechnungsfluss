import io

import pytest
from pypdf import PdfReader

from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.synthetic.render import Layout, render_pdf
from tests.conftest import make_invoice


def _text(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


@pytest.mark.parametrize("layout", [Layout.CLASSIC, Layout.COMPACT])
def test_vector_layouts_print_every_reviewable_field(invoice: Invoice, layout: Layout) -> None:
    text = _text(render_pdf(invoice, layout))
    for value in (
        "SYNTHETISCH",
        invoice.number,
        "01.10.2026",
        "31.10.2026",
        "PO-DE-1001",
        "04011000-12345-34",
        "DE999999999",
        "DE888888888",
        "Deutschland",
        "rechnung@muster-schrauben.example",
        "ap@nordwerk.example",
        "Erika Beispiel",
        "+49 30 0000000",
        "erika@muster-schrauben.example",
        "DE89370400440532013000",
        "148,75 EUR",
        "H87",
        "Werkstraße 7",
    ):
        assert value in text, (layout, value)


def test_order_line_reference_is_printed(invoice: Invoice) -> None:
    line = invoice.lines[0].model_copy(update={"order_line_ref": "7"})
    text = _text(render_pdf(invoice.model_copy(update={"lines": (line,)})))
    assert "Best.-Pos." in text
    assert "Netto\n1\n7\nSechskantschraube" in text  # pypdf: one table cell per line


def test_scan_has_no_text_layer(invoice: Invoice) -> None:
    pdf = render_pdf(invoice, Layout.SCAN)
    assert _text(pdf).strip() == ""
    assert len(pdf) > 50_000  # an embedded page image, not text


@pytest.mark.parametrize("layout", list(Layout))
def test_rendering_is_deterministic(invoice: Invoice, layout: Layout) -> None:
    assert render_pdf(invoice, layout, seed="a") == render_pdf(invoice, layout, seed="a")


def test_scan_seed_changes_the_noise(invoice: Invoice) -> None:
    assert render_pdf(invoice, Layout.SCAN, seed="a") != render_pdf(invoice, Layout.SCAN, seed="b")


def test_long_invoices_continue_on_further_pages() -> None:
    lines = make_invoice().lines * 60
    relabelled = tuple(ln.model_copy(update={"line_id": str(i + 1)}) for i, ln in enumerate(lines))
    reader = PdfReader(io.BytesIO(render_pdf(make_invoice(lines=relabelled))))
    assert len(reader.pages) >= 2
    assert "Fortsetzung" in reader.pages[1].extract_text()
