"""OASIS UBL 2.1 Invoice <-> canonical model (EN 16931 subset).

Element order follows the UBL 2.1 Invoice schema, which KoSIT checks. The
serializer exists so the synthetic generator can produce XRechnung-UBL inputs.
The parser is the production path for UBL invoices.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from lxml import etree

from rechnungsfluss.domain.invoice import (
    Contact,
    Invoice,
    InvoiceLine,
    Party,
    PostalAddress,
    Totals,
    VatBreakdown,
)
from rechnungsfluss.formats.cii import PEPPOL_BILLING_PROCESS
from rechnungsfluss.formats.common import (
    ParseError,
    child_text,
    fmt_amount,
    fmt_quantity,
    parse_xml,
    req_text,
)

INV = "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
NS = {"inv": INV, "cac": CAC, "cbc": CBC}


def _a(tag: str) -> str:
    return f"{{{CAC}}}{tag}"


def _b(tag: str) -> str:
    return f"{{{CBC}}}{tag}"


def _sub(parent: etree._Element, tag: str, text: str | None = None, **attrs: str) -> etree._Element:
    el = etree.SubElement(parent, tag, attrs)
    if text is not None:
        el.text = text
    return el


def _vat_scheme(parent: etree._Element) -> None:
    _sub(_sub(parent, _a("TaxScheme")), _b("ID"), "VAT")


def _party(parent: etree._Element, tag: str, party: Party) -> None:
    p = _sub(_sub(parent, _a(tag)), _a("Party"))
    if party.electronic_address:
        _sub(p, _b("EndpointID"), party.electronic_address, schemeID="EM")
    addr = _sub(p, _a("PostalAddress"))
    a = party.address
    if a.line1:
        _sub(addr, _b("StreetName"), a.line1)
    if a.city:
        _sub(addr, _b("CityName"), a.city)
    if a.post_code:
        _sub(addr, _b("PostalZone"), a.post_code)
    _sub(_sub(addr, _a("Country")), _b("IdentificationCode"), a.country_code)
    if party.vat_id:
        tax = _sub(p, _a("PartyTaxScheme"))
        _sub(tax, _b("CompanyID"), party.vat_id)
        _vat_scheme(tax)
    _sub(_sub(p, _a("PartyLegalEntity")), _b("RegistrationName"), party.name)
    if party.contact is not None:
        c = _sub(p, _a("Contact"))
        if party.contact.name:
            _sub(c, _b("Name"), party.contact.name)
        if party.contact.phone:
            _sub(c, _b("Telephone"), party.contact.phone)
        if party.contact.email:
            _sub(c, _b("ElectronicMail"), party.contact.email)


def to_ubl(invoice: Invoice, *, specification_id: str) -> bytes:
    cur = invoice.currency
    # None = default namespace; lxml supports it, lxml-stubs types the key as str only.
    nsmap = {None: INV, "cac": CAC, "cbc": CBC}
    root = etree.Element(f"{{{INV}}}Invoice", nsmap=nsmap)  # type: ignore[arg-type]
    _sub(root, _b("CustomizationID"), specification_id)
    _sub(root, _b("ProfileID"), PEPPOL_BILLING_PROCESS)
    _sub(root, _b("ID"), invoice.number)
    _sub(root, _b("IssueDate"), invoice.issue_date.isoformat())
    if invoice.due_date:
        _sub(root, _b("DueDate"), invoice.due_date.isoformat())
    _sub(root, _b("InvoiceTypeCode"), invoice.type_code)
    _sub(root, _b("DocumentCurrencyCode"), cur)
    if invoice.buyer_reference:
        _sub(root, _b("BuyerReference"), invoice.buyer_reference)
    if invoice.purchase_order_ref:
        _sub(_sub(root, _a("OrderReference")), _b("ID"), invoice.purchase_order_ref)
    _party(root, "AccountingSupplierParty", invoice.seller)
    _party(root, "AccountingCustomerParty", invoice.buyer)

    means = _sub(root, _a("PaymentMeans"))
    _sub(means, _b("PaymentMeansCode"), invoice.payment_means_code)
    if invoice.seller_iban:
        _sub(_sub(means, _a("PayeeFinancialAccount")), _b("ID"), invoice.seller_iban)

    t = invoice.totals
    tax_total = _sub(root, _a("TaxTotal"))
    _sub(tax_total, _b("TaxAmount"), fmt_amount(t.vat_total), currencyID=cur)
    for vat in invoice.vat_breakdown:
        sub = _sub(tax_total, _a("TaxSubtotal"))
        _sub(sub, _b("TaxableAmount"), fmt_amount(vat.taxable_amount), currencyID=cur)
        _sub(sub, _b("TaxAmount"), fmt_amount(vat.tax_amount), currencyID=cur)
        cat = _sub(sub, _a("TaxCategory"))
        _sub(cat, _b("ID"), vat.category)
        if vat.rate is not None:
            _sub(cat, _b("Percent"), fmt_amount(vat.rate))
        _vat_scheme(cat)

    totals = _sub(root, _a("LegalMonetaryTotal"))
    _sub(totals, _b("LineExtensionAmount"), fmt_amount(t.line_net_sum), currencyID=cur)
    _sub(totals, _b("TaxExclusiveAmount"), fmt_amount(t.total_without_vat), currencyID=cur)
    _sub(totals, _b("TaxInclusiveAmount"), fmt_amount(t.total_with_vat), currencyID=cur)
    _sub(totals, _b("PayableAmount"), fmt_amount(t.amount_due), currencyID=cur)

    for line in invoice.lines:
        el = _sub(root, _a("InvoiceLine"))
        _sub(el, _b("ID"), line.line_id)
        _sub(el, _b("InvoicedQuantity"), fmt_quantity(line.quantity), unitCode=line.unit_code)
        _sub(el, _b("LineExtensionAmount"), fmt_amount(line.net_amount), currencyID=cur)
        item = _sub(el, _a("Item"))
        _sub(item, _b("Name"), line.description or "-")
        cat = _sub(item, _a("ClassifiedTaxCategory"))
        _sub(cat, _b("ID"), line.vat_category)
        if line.vat_rate is not None:
            _sub(cat, _b("Percent"), fmt_amount(line.vat_rate))
        _vat_scheme(cat)
        _sub(_sub(el, _a("Price")), _b("PriceAmount"), fmt_amount(line.net_price), currencyID=cur)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


# --- parsing ----------------------------------------------------------------


def _date(el: etree._Element, path: str) -> dt.date | None:
    raw = child_text(el, path, NS)
    if raw is None:
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise ParseError(f"bad date {raw!r} at {path}") from exc


def _dec(el: etree._Element, path: str) -> Decimal:
    return Decimal(req_text(el, path, NS))


def _opt_dec(el: etree._Element, path: str) -> Decimal | None:
    raw = child_text(el, path, NS)
    return None if raw is None else Decimal(raw)


def _vat_id(party: etree._Element) -> str | None:
    """BT-31/BT-48: the PartyTaxScheme whose TaxScheme is VAT (others are e.g. FC tax numbers)."""
    for scheme in party.findall("cac:PartyTaxScheme", NS):
        if child_text(scheme, "cac:TaxScheme/cbc:ID", NS) == "VAT":
            return child_text(scheme, "cbc:CompanyID", NS)
    return None


def _parse_party(root: etree._Element, tag: str) -> Party:
    p = root.find(f"cac:{tag}/cac:Party", NS)
    if p is None:
        raise ParseError(f"missing {tag}")
    addr = p.find("cac:PostalAddress", NS)
    if addr is None:
        raise ParseError(f"missing PostalAddress in {tag}")
    contact_el = p.find("cac:Contact", NS)
    return Party(
        name=req_text(p, "cac:PartyLegalEntity/cbc:RegistrationName", NS),
        vat_id=_vat_id(p),
        address=PostalAddress(
            line1=child_text(addr, "cbc:StreetName", NS),
            city=child_text(addr, "cbc:CityName", NS),
            post_code=child_text(addr, "cbc:PostalZone", NS),
            country_code=req_text(addr, "cac:Country/cbc:IdentificationCode", NS),
        ),
        electronic_address=child_text(p, "cbc:EndpointID", NS),
        contact=Contact(
            name=child_text(contact_el, "cbc:Name", NS),
            phone=child_text(contact_el, "cbc:Telephone", NS),
            email=child_text(contact_el, "cbc:ElectronicMail", NS),
        )
        if contact_el is not None
        else None,
    )


def from_ubl(data: bytes) -> Invoice:
    root = parse_xml(data)
    if root.tag != f"{{{INV}}}Invoice":
        raise ParseError(f"not a UBL invoice: root is {root.tag}")
    totals = root.find("cac:LegalMonetaryTotal", NS)
    tax_total = root.find("cac:TaxTotal", NS)
    if totals is None or tax_total is None:
        raise ParseError("missing LegalMonetaryTotal or TaxTotal")

    lines = []
    for el in root.findall("cac:InvoiceLine", NS):
        qty = el.find("cbc:InvoicedQuantity", NS)
        if qty is None or qty.text is None:
            raise ParseError("line without InvoicedQuantity")
        lines.append(
            InvoiceLine(
                line_id=req_text(el, "cbc:ID", NS),
                quantity=Decimal(qty.text.strip()),
                unit_code=qty.get("unitCode", ""),
                net_price=_dec(el, "cac:Price/cbc:PriceAmount"),
                net_amount=_dec(el, "cbc:LineExtensionAmount"),
                vat_category=req_text(el, "cac:Item/cac:ClassifiedTaxCategory/cbc:ID", NS),
                vat_rate=_opt_dec(el, "cac:Item/cac:ClassifiedTaxCategory/cbc:Percent"),
                description=child_text(el, "cac:Item/cbc:Name", NS),
            )
        )
    breakdown = [
        VatBreakdown(
            taxable_amount=_dec(sub, "cbc:TaxableAmount"),
            tax_amount=_dec(sub, "cbc:TaxAmount"),
            category=req_text(sub, "cac:TaxCategory/cbc:ID", NS),
            rate=_opt_dec(sub, "cac:TaxCategory/cbc:Percent"),
        )
        for sub in tax_total.findall("cac:TaxSubtotal", NS)
    ]
    issue = _date(root, "cbc:IssueDate")
    if issue is None:
        raise ParseError("missing cbc:IssueDate")
    try:
        return Invoice(
            number=req_text(root, "cbc:ID", NS),
            issue_date=issue,
            type_code=req_text(root, "cbc:InvoiceTypeCode", NS),
            currency=req_text(root, "cbc:DocumentCurrencyCode", NS),
            due_date=_date(root, "cbc:DueDate"),
            buyer_reference=child_text(root, "cbc:BuyerReference", NS),
            purchase_order_ref=child_text(root, "cac:OrderReference/cbc:ID", NS),
            seller=_parse_party(root, "AccountingSupplierParty"),
            seller_iban=child_text(root, "cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID", NS),
            payment_means_code=child_text(root, "cac:PaymentMeans/cbc:PaymentMeansCode", NS)
            or "58",
            specification_id=child_text(root, "cbc:CustomizationID", NS),
            buyer=_parse_party(root, "AccountingCustomerParty"),
            lines=tuple(lines),
            vat_breakdown=tuple(breakdown),
            totals=Totals(
                line_net_sum=_dec(totals, "cbc:LineExtensionAmount"),
                total_without_vat=_dec(totals, "cbc:TaxExclusiveAmount"),
                vat_total=_dec(tax_total, "cbc:TaxAmount"),
                total_with_vat=_dec(totals, "cbc:TaxInclusiveAmount"),
                amount_due=_dec(totals, "cbc:PayableAmount"),
            ),
        )
    except ValueError as exc:
        raise ParseError(f"invoice does not fit the canonical model: {exc}") from exc
