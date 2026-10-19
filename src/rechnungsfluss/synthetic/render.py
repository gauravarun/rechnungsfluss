"""Render a canonical invoice as a human-readable PDF (synthetic data only).

One layout description, two backends:

- ``classic`` / ``compact``: vector PDFs with a text layer (reportlab)
- ``scan``: the classic layout drawn onto a 150-dpi grayscale image, rotated
  slightly, with seeded grain and JPEG compression, embedded as the only page
  content. No text layer, like a scanned paper invoice.

Every field of the canonical invoice that an AP clerk would read is printed,
so extraction is scored only on information that is actually on the page.
Every page carries a visible "SYNTHETISCH" marker. Output is deterministic for
a given invoice, layout and seed.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

import reportlab
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from rechnungsfluss.domain.invoice import Invoice, Party

COUNTRY_NAMES = {"DE": "Deutschland", "AT": "Österreich"}
PAGE_W_MM, PAGE_H_MM = 210.0, 297.0
SCAN_DPI = 150
MARKER = "SYNTHETISCH - KEINE ECHTE RECHNUNG"


class Layout(StrEnum):
    CLASSIC = "classic"
    COMPACT = "compact"
    SCAN = "scan"


@dataclass(frozen=True, slots=True)
class Op:
    """Draw ``text`` with its baseline at (x, y) mm from the top-left corner."""

    x: float
    y: float
    text: str
    size: float = 9
    bold: bool = False
    align: str = "left"  # left | right


def _amount(value: Decimal, currency: str = "") -> str:
    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} {currency}".strip()


def _qty(value: Decimal) -> str:
    return f"{value.normalize():f}".replace(".", ",")


def _rate(rate: Decimal | None, category: str) -> str:
    return f"{rate.normalize():f} %" if rate is not None else category


def _address_lines(party: Party) -> list[str]:
    a = party.address
    lines = [party.name]
    if a.line1:
        lines.append(a.line1)
    city = f"{a.post_code or ''} {a.city or ''}".strip()
    if city:
        lines.append(city)
    lines.append(COUNTRY_NAMES.get(a.country_code, a.country_code))
    return lines


def _ops(inv: Invoice, layout: Layout) -> list[list[Op]]:
    """Layout as pages of draw operations. ``scan`` uses the classic arrangement."""
    compact = layout is Layout.COMPACT
    ops: list[Op] = [Op(190, 10, MARKER, 8, True, "right")]
    s, b = inv.seller, inv.buyer

    # Seller block: top-left (classic) or top-right (compact).
    sx, salign = (190.0, "right") if compact else (20.0, "left")
    y = 18.0
    for i, text in enumerate(_address_lines(s)):
        ops.append(Op(sx, y, text, 11 if i == 0 else 8.5, i == 0, salign))
        y += 4.5
    contact = s.contact
    for extra in (
        f"USt-IdNr.: {s.vat_id}" if s.vat_id else None,
        f"E-Mail: {s.electronic_address}" if s.electronic_address else None,
        f"Ansprechpartner: {contact.name}" if contact and contact.name else None,
        f"Tel.: {contact.phone}" if contact and contact.phone else None,
        f"Kontakt-E-Mail: {contact.email}" if contact and contact.email else None,
    ):
        if extra:
            ops.append(Op(sx, y, extra, 8, False, salign))
            y += 4
    seller_end = y

    # Buyer block.
    y = 58.0 if compact else 62.0
    ops.append(Op(20, y - 5, "Rechnungsempfänger", 7))
    for text in _address_lines(b):
        ops.append(Op(20, y, text, 10))
        y += 5
    if b.vat_id:
        ops.append(Op(20, y, f"USt-IdNr.: {b.vat_id}", 8))
        y += 4.5
    if b.electronic_address:
        ops.append(Op(20, y, f"E-Mail: {b.electronic_address}", 8))
        y += 4.5

    # Header data: below the buyer (classic) or in a right-hand box (compact).
    meta = [
        ("Rechnungsnummer", inv.number),
        ("Rechnungsdatum", inv.issue_date.strftime("%d.%m.%Y")),
        ("Fällig am", inv.due_date.strftime("%d.%m.%Y") if inv.due_date else None),
        ("Ihre Bestellung", inv.purchase_order_ref),
        ("Käuferreferenz", inv.buyer_reference),
        ("Währung", inv.currency),
    ]
    if compact:
        my = max(58.0, seller_end + 12)  # header box starts below the right-hand seller block
        ops.append(Op(120, my - 6, "RECHNUNG", 13, True))
        for label, value in meta:
            if value:
                ops.append(Op(120, my, label, 8))
                ops.append(Op(190, my, value, 8.5, True, "right"))
                my += 4.8
        y = max(y, my) + 10
    else:
        y = max(y, 95.0)
        ops.append(Op(20, y, "Rechnung", 14, True))
        for label, value in meta:
            if value:
                y += 5.5
                ops.append(Op(20, y, f"{label}:", 9.5))
                ops.append(Op(78, y, value, 9.5))
        y += 12

    # Line items (repeat the header on continuation pages).
    cols = {
        "pos": 20,
        "ref": 29,
        "desc": 48,
        "qty": 125,
        "unit": 128,
        "price": 160,
        "rate": 165,
        "net": 190,
    }
    header = [
        ("pos", "Pos.", "left"),
        ("ref", "Best.-Pos.", "left"),
        ("desc", "Bezeichnung", "left"),
        ("qty", "Menge", "right"),
        ("unit", "Einheit", "left"),
        ("price", "Einzelpreis", "right"),
        ("rate", "USt.", "left"),
        ("net", "Netto", "right"),
    ]
    pages: list[list[Op]] = []

    def table_header(at: float, page: list[Op]) -> None:
        for key, text, align in header:
            page.append(Op(cols[key], at, text, 7.5, True, align))

    table_header(y, ops)
    for line in inv.lines:
        y += 5.2
        if y > 250:
            pages.append(ops)
            ops = [
                Op(190, 10, MARKER, 8, True, "right"),
                Op(20, 18, f"Rechnung {inv.number} - Fortsetzung", 9, True),
            ]
            y = 28.0
            table_header(y, ops)
            y += 5.2
        ops += [
            Op(cols["pos"], y, line.line_id, 8),
            Op(cols["ref"], y, line.order_line_ref or "", 8),
            Op(cols["desc"], y, (line.description or "")[:48], 8),
            Op(cols["qty"], y, _qty(line.quantity), 8, align="right"),
            Op(cols["unit"], y, line.unit_code, 8),
            Op(cols["price"], y, _amount(line.net_price), 8, align="right"),
            Op(cols["rate"], y, _rate(line.vat_rate, line.vat_category), 8),
            Op(cols["net"], y, _amount(line.net_amount), 8, align="right"),
        ]

    # Totals (new page if they would not fit).
    t = inv.totals
    rows = [
        ("Summe Positionen", t.line_net_sum, False),  # BT-106
        ("Summe netto", t.total_without_vat, False),  # BT-109
    ]
    for vat in inv.vat_breakdown:
        label = (
            f"USt. {_rate(vat.rate, vat.category)} auf {_amount(vat.taxable_amount, inv.currency)}"
        )
        rows.append((label, vat.tax_amount, False))
    rows += [("Rechnungsbetrag", t.total_with_vat, False), ("Zahlbetrag", t.amount_due, True)]
    y += 10
    if y + 5.5 * len(rows) > 270:
        pages.append(ops)
        ops = [Op(190, 10, MARKER, 8, True, "right")]
        y = 28.0
    tx = 20 if compact else 100
    for label, amount, bold in rows:
        ops.append(Op(tx, y, label, 9, bold))
        ops.append(Op(190, y, _amount(amount, inv.currency), 9, bold, "right"))
        y += 5.5
    if inv.seller_iban:
        ops.append(
            Op(20, 280, f"Bitte überweisen Sie den Zahlbetrag auf IBAN {inv.seller_iban}.", 8)
        )
    pages.append(ops)
    return pages


def _vector_pdf(pages: list[list[Op]], title: str) -> bytes:
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4, invariant=True)  # invariant: byte-identical output per input
    c.setTitle(title)
    _, height = A4
    for page in pages:
        for op in page:
            c.setFont("Helvetica-Bold" if op.bold else "Helvetica", op.size)
            x, y = op.x * mm, height - op.y * mm
            if op.align == "right":
                c.drawRightString(x, y, op.text)
            else:
                c.drawString(x, y, op.text)
        c.showPage()
    c.save()
    return buf.getvalue()


def _font_path(bold: bool) -> str:
    """Bitstream Vera, shipped with reportlab: covers German umlauts and ß on every OS."""
    return str(Path(reportlab.__file__).parent / "fonts" / ("VeraBd.ttf" if bold else "Vera.ttf"))


def _scan_image(page: list[Op], rng: random.Random) -> Image.Image:
    px = SCAN_DPI / 25.4
    img = Image.new("L", (round(PAGE_W_MM * px), round(PAGE_H_MM * px)), 255)
    draw = ImageDraw.Draw(img)
    fonts: dict[tuple[float, bool], ImageFont.FreeTypeFont] = {}
    for op in page:
        key = (op.size, op.bold)
        if key not in fonts:
            fonts[key] = ImageFont.truetype(_font_path(op.bold), round(op.size * 0.3528 * px))
        anchor = "rs" if op.align == "right" else "ls"  # baseline, as in the PDF backend
        draw.text((op.x * px, op.y * px), op.text, fill=30, font=fonts[key], anchor=anchor)
    img = img.rotate(rng.uniform(-1.2, 1.2), resample=Image.Resampling.BICUBIC, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(radius=0.6))
    grain = Image.frombytes("L", img.size, rng.randbytes(img.size[0] * img.size[1]))
    return Image.blend(img, grain, alpha=0.06)


def _scan_pdf(pages: list[list[Op]], title: str, seed: str) -> bytes:
    rng = random.Random(seed)
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4, invariant=True)
    c.setTitle(title)
    width, height = A4
    for page in pages:
        jpeg = io.BytesIO()
        _scan_image(page, rng).save(jpeg, format="JPEG", quality=70)
        jpeg.seek(0)
        c.drawImage(ImageReader(jpeg), 0, 0, width=width, height=height)
        c.showPage()
    c.save()
    return buf.getvalue()


def render_pdf(invoice: Invoice, layout: Layout = Layout.CLASSIC, *, seed: str = "") -> bytes:
    pages = _ops(invoice, Layout.CLASSIC if layout is Layout.SCAN else layout)
    title = f"Rechnung {invoice.number}"
    if layout is Layout.SCAN:
        return _scan_pdf(pages, title, seed or invoice.number)
    return _vector_pdf(pages, title)
