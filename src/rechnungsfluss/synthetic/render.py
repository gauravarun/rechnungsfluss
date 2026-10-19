"""Render a canonical invoice as a human-readable PDF (synthetic data only).

This is the visual part of a ZUGFeRD hybrid and, in W4, the input for LLM
extraction. Layout is plain German business-letter style. Every page carries a
visible "SYNTHETISCH" marker so a generated file can never pass as real.
"""

from __future__ import annotations

import io
from decimal import Decimal

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from rechnungsfluss.domain.invoice import Invoice


def _eur(value: Decimal, currency: str) -> str:
    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} {currency}"


def render_pdf(invoice: Invoice) -> bytes:
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=A4, invariant=True)  # invariant: byte-identical output per input
    width, height = A4
    s, b = invoice.seller, invoice.buyer

    c.setTitle(f"Rechnung {invoice.number}")
    c.setFont("Helvetica-Bold", 9)
    c.drawRightString(width - 20 * mm, height - 12 * mm, "SYNTHETISCH - KEINE ECHTE RECHNUNG")

    y = height - 25 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(20 * mm, y, s.name)
    c.setFont("Helvetica", 9)
    for text in (
        s.address.line1,
        f"{s.address.post_code or ''} {s.address.city or ''}".strip(),
        f"USt-IdNr.: {s.vat_id}" if s.vat_id else None,
    ):
        if text:
            y -= 4.5 * mm
            c.drawString(20 * mm, y, text)

    y = height - 60 * mm
    c.setFont("Helvetica", 10)
    for text in (b.name, b.address.line1, f"{b.address.post_code or ''} {b.address.city or ''}"):
        if text and text.strip():
            c.drawString(20 * mm, y, text.strip())
            y -= 5 * mm

    y = height - 95 * mm
    c.setFont("Helvetica-Bold", 14)
    c.drawString(20 * mm, y, "Rechnung")
    c.setFont("Helvetica", 10)
    meta = [
        ("Rechnungsnummer", invoice.number),
        ("Rechnungsdatum", invoice.issue_date.strftime("%d.%m.%Y")),
        ("Fällig am", invoice.due_date.strftime("%d.%m.%Y") if invoice.due_date else None),
        ("Ihre Bestellung", invoice.purchase_order_ref),
        ("Leitweg-ID / Käuferreferenz", invoice.buyer_reference),
    ]
    for label, value in meta:
        if value:
            y -= 5.5 * mm
            c.drawString(20 * mm, y, f"{label}:")
            c.drawString(75 * mm, y, value)

    y -= 12 * mm
    c.setFont("Helvetica-Bold", 9)
    cols = (20, 30, 110, 130, 150, 172)
    for x, head in zip(
        cols, ("Pos.", "Bezeichnung", "Menge", "Einheit", "Einzelpreis", "Netto"), strict=True
    ):
        c.drawString(x * mm, y, head)
    c.setFont("Helvetica", 9)
    for line in invoice.lines:
        y -= 5.5 * mm
        if y < 40 * mm:
            c.showPage()
            c.setFont("Helvetica", 9)
            y = height - 25 * mm
        c.drawString(cols[0] * mm, y, line.line_id)
        c.drawString(cols[1] * mm, y, (line.description or "")[:45])
        c.drawRightString(
            (cols[2] + 15) * mm, y, f"{line.quantity.normalize():f}".replace(".", ",")
        )
        c.drawString(cols[3] * mm, y, line.unit_code)
        c.drawRightString((cols[4] + 18) * mm, y, _eur(line.net_price, invoice.currency))
        c.drawRightString((cols[5] + 18) * mm, y, _eur(line.net_amount, invoice.currency))

    t = invoice.totals
    y -= 10 * mm
    rows = [("Summe netto", t.total_without_vat)]
    rows += [
        (
            f"USt. {vat.rate.normalize():f} %" if vat.rate is not None else f"USt. {vat.category}",
            vat.tax_amount,
        )
        for vat in invoice.vat_breakdown
    ]
    rows += [("Rechnungsbetrag", t.total_with_vat), ("Zahlbetrag", t.amount_due)]
    for label, amount in rows:
        c.setFont("Helvetica-Bold" if label == "Zahlbetrag" else "Helvetica", 10)
        c.drawString(130 * mm, y, label)
        c.drawRightString(190 * mm, y, _eur(amount, invoice.currency))
        y -= 5.5 * mm

    if invoice.seller_iban:
        c.setFont("Helvetica", 9)
        c.drawString(20 * mm, 25 * mm, f"Bitte überweisen Sie auf IBAN {invoice.seller_iban}.")
    c.showPage()
    c.save()
    return buf.getvalue()
