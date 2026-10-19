"""Hypothesis strategies for synthetic invoices (always arithmetically consistent)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from hypothesis import strategies as st

from rechnungsfluss.domain.invoice import Contact, Invoice, Party, PostalAddress
from rechnungsfluss.synthetic.builder import LineSpec, build_invoice

_text = (
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters=" -.&äöüßÄÖÜ"),
        min_size=1,
        max_size=40,
    )
    .map(str.strip)
    .filter(bool)
)

_rates = st.sampled_from([("S", Decimal("19")), ("S", Decimal("7")), ("Z", Decimal("0"))])


@st.composite
def line_specs(draw: st.DrawFn) -> LineSpec:
    category, rate = draw(_rates)
    return LineSpec(
        description=draw(_text),
        quantity=draw(st.decimals(min_value=Decimal("0.5"), max_value=Decimal("500"), places=2)),
        unit_code=draw(st.sampled_from(["H87", "C62", "KGM", "HUR", "MTR"])),
        net_price=draw(st.decimals(min_value=Decimal("0.01"), max_value=Decimal("9999"), places=2)),
        vat_category=category,
        vat_rate=rate,
        order_line_ref=draw(st.none() | st.from_regex(r"[1-9][0-9]{0,2}", fullmatch=True)),
    )


def _party(draw: st.DrawFn, *, seller: bool) -> Party:
    return Party(
        name=draw(_text),
        vat_id="DE" + draw(st.from_regex(r"[0-9]{9}", fullmatch=True)),
        address=PostalAddress(
            line1=draw(_text),
            city=draw(_text),
            post_code=draw(st.from_regex(r"[0-9]{5}", fullmatch=True)),
            country_code="DE",
        ),
        electronic_address="billing@example.com",
        contact=Contact(name=draw(_text), phone="+49 30 0000000", email="ap@example.com")
        if seller
        else None,
    )


@st.composite
def invoices(draw: st.DrawFn) -> Invoice:
    return build_invoice(
        number=draw(st.from_regex(r"RE-[0-9]{4}-[0-9]{5}", fullmatch=True)),
        issue_date=draw(st.dates(min_value=dt.date(2025, 1, 1), max_value=dt.date(2026, 12, 31))),
        seller=_party(draw, seller=True),
        buyer=_party(draw, seller=False),
        lines=draw(st.lists(line_specs(), min_size=1, max_size=6)),
        buyer_reference=draw(st.from_regex(r"[0-9]{2,12}-[0-9A-Z]{1,30}-[0-9]{2}", fullmatch=True)),
        purchase_order_ref=draw(st.none() | st.from_regex(r"PO-[A-Z]{2}-[0-9]{4}", fullmatch=True)),
        seller_iban="DE89370400440532013000",
    )
