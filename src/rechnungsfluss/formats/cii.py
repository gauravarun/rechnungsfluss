"""UN/CEFACT Cross Industry Invoice (CII D16B) <-> canonical model.

Covers the EN 16931 subset modelled in ``domain/invoice.py``. Element order
follows the CII D16B schema, which the KoSIT validator checks. Used for
XRechnung-CII and ZUGFeRD/Factur-X input, and to serialize LLM extractions for
official validation (ADR-0002).
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
from rechnungsfluss.formats.common import (
    ParseError,
    child_text,
    fmt_amount,
    fmt_quantity,
    parse_xml,
    req_text,
)

RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"
QDT = "urn:un:unece:uncefact:data:standard:QualifiedDataType:100"
NS = {"rsm": RSM, "ram": RAM, "udt": UDT, "qdt": QDT}

# BT-23 business process used by XRechnung / Peppol BIS Billing.
PEPPOL_BILLING_PROCESS = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"


def _r(tag: str) -> str:
    return f"{{{RAM}}}{tag}"


def _sub(parent: etree._Element, tag: str, text: str | None = None, **attrs: str) -> etree._Element:
    el = etree.SubElement(parent, tag, attrs)
    if text is not None:
        el.text = text
    return el


def _date(parent: etree._Element, tag: str, value: dt.date) -> None:
    wrapper = _sub(parent, _r(tag))
    _sub(wrapper, f"{{{UDT}}}DateTimeString", value.strftime("%Y%m%d"), format="102")


def _party(parent: etree._Element, tag: str, party: Party) -> None:
    el = _sub(parent, _r(tag))
    _sub(el, _r("Name"), party.name)
    if party.contact is not None:
        c = party.contact
        contact = _sub(el, _r("DefinedTradeContact"))
        if c.name:
            _sub(contact, _r("PersonName"), c.name)
        if c.phone:
            _sub(
                _sub(contact, _r("TelephoneUniversalCommunication")), _r("CompleteNumber"), c.phone
            )
        if c.email:
            _sub(_sub(contact, _r("EmailURIUniversalCommunication")), _r("URIID"), c.email)
    addr = _sub(el, _r("PostalTradeAddress"))
    a = party.address
    if a.post_code:
        _sub(addr, _r("PostcodeCode"), a.post_code)
    if a.line1:
        _sub(addr, _r("LineOne"), a.line1)
    if a.city:
        _sub(addr, _r("CityName"), a.city)
    _sub(addr, _r("CountryID"), a.country_code)
    if party.electronic_address:
        uri = _sub(el, _r("URIUniversalCommunication"))
        _sub(uri, _r("URIID"), party.electronic_address, schemeID="EM")
    if party.vat_id:
        reg = _sub(el, _r("SpecifiedTaxRegistration"))
        _sub(reg, _r("ID"), party.vat_id, schemeID="VA")


def _tax(
    parent: etree._Element,
    category: str,
    rate: Decimal | None,
    *,
    calculated: Decimal | None = None,
    basis: Decimal | None = None,
) -> None:
    tax = _sub(parent, _r("ApplicableTradeTax"))
    if calculated is not None:
        _sub(tax, _r("CalculatedAmount"), fmt_amount(calculated))
    _sub(tax, _r("TypeCode"), "VAT")
    if basis is not None:
        _sub(tax, _r("BasisAmount"), fmt_amount(basis))
    _sub(tax, _r("CategoryCode"), category)
    if rate is not None:
        _sub(tax, _r("RateApplicablePercent"), fmt_amount(rate))


def to_cii(invoice: Invoice, *, specification_id: str) -> bytes:
    """Serialize ``invoice`` as CII with the given BT-24 specification identifier."""
    root = etree.Element(f"{{{RSM}}}CrossIndustryInvoice", nsmap=NS)

    ctx = _sub(root, f"{{{RSM}}}ExchangedDocumentContext")
    _sub(
        _sub(ctx, _r("BusinessProcessSpecifiedDocumentContextParameter")),
        _r("ID"),
        PEPPOL_BILLING_PROCESS,
    )
    _sub(_sub(ctx, _r("GuidelineSpecifiedDocumentContextParameter")), _r("ID"), specification_id)

    doc = _sub(root, f"{{{RSM}}}ExchangedDocument")
    _sub(doc, _r("ID"), invoice.number)
    _sub(doc, _r("TypeCode"), invoice.type_code)
    _date(doc, "IssueDateTime", invoice.issue_date)

    tx = _sub(root, f"{{{RSM}}}SupplyChainTradeTransaction")
    for line in invoice.lines:
        item = _sub(tx, _r("IncludedSupplyChainTradeLineItem"))
        _sub(_sub(item, _r("AssociatedDocumentLineDocument")), _r("LineID"), line.line_id)
        _sub(_sub(item, _r("SpecifiedTradeProduct")), _r("Name"), line.description or "-")
        agreement = _sub(item, _r("SpecifiedLineTradeAgreement"))
        price = _sub(agreement, _r("NetPriceProductTradePrice"))
        _sub(price, _r("ChargeAmount"), fmt_amount(line.net_price))
        delivery = _sub(item, _r("SpecifiedLineTradeDelivery"))
        _sub(delivery, _r("BilledQuantity"), fmt_quantity(line.quantity), unitCode=line.unit_code)
        settlement = _sub(item, _r("SpecifiedLineTradeSettlement"))
        _tax(settlement, line.vat_category, line.vat_rate)
        summation = _sub(settlement, _r("SpecifiedTradeSettlementLineMonetarySummation"))
        _sub(summation, _r("LineTotalAmount"), fmt_amount(line.net_amount))

    agreement = _sub(tx, _r("ApplicableHeaderTradeAgreement"))
    if invoice.buyer_reference:
        _sub(agreement, _r("BuyerReference"), invoice.buyer_reference)
    _party(agreement, "SellerTradeParty", invoice.seller)
    _party(agreement, "BuyerTradeParty", invoice.buyer)
    if invoice.purchase_order_ref:
        order = _sub(agreement, _r("BuyerOrderReferencedDocument"))
        _sub(order, _r("IssuerAssignedID"), invoice.purchase_order_ref)

    _sub(tx, _r("ApplicableHeaderTradeDelivery"))

    settlement = _sub(tx, _r("ApplicableHeaderTradeSettlement"))
    _sub(settlement, _r("InvoiceCurrencyCode"), invoice.currency)
    means = _sub(settlement, _r("SpecifiedTradeSettlementPaymentMeans"))
    _sub(means, _r("TypeCode"), invoice.payment_means_code)
    if invoice.seller_iban:
        account = _sub(means, _r("PayeePartyCreditorFinancialAccount"))
        _sub(account, _r("IBANID"), invoice.seller_iban)
    for vat in invoice.vat_breakdown:
        _tax(
            settlement, vat.category, vat.rate, calculated=vat.tax_amount, basis=vat.taxable_amount
        )
    if invoice.due_date:
        _date(
            _sub(settlement, _r("SpecifiedTradePaymentTerms")), "DueDateDateTime", invoice.due_date
        )
    t = invoice.totals
    sums = _sub(settlement, _r("SpecifiedTradeSettlementHeaderMonetarySummation"))
    _sub(sums, _r("LineTotalAmount"), fmt_amount(t.line_net_sum))
    _sub(sums, _r("TaxBasisTotalAmount"), fmt_amount(t.total_without_vat))
    _sub(sums, _r("TaxTotalAmount"), fmt_amount(t.vat_total), currencyID=invoice.currency)
    _sub(sums, _r("GrandTotalAmount"), fmt_amount(t.total_with_vat))
    _sub(sums, _r("DuePayableAmount"), fmt_amount(t.amount_due))

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


# --- parsing ----------------------------------------------------------------


def _parse_date(el: etree._Element | None, path: str) -> dt.date | None:
    if el is None:
        return None
    raw = child_text(el, f"{path}/udt:DateTimeString", NS)
    if raw is None:
        return None
    try:
        return dt.datetime.strptime(raw, "%Y%m%d").date()  # noqa: DTZ007 (date only)
    except ValueError as exc:
        raise ParseError(f"bad date {raw!r} at {path}") from exc


def _parse_party(el: etree._Element) -> Party:
    contact_el = el.find("ram:DefinedTradeContact", NS)
    contact = None
    if contact_el is not None:
        contact = Contact(
            name=child_text(contact_el, "ram:PersonName", NS),
            phone=child_text(
                contact_el, "ram:TelephoneUniversalCommunication/ram:CompleteNumber", NS
            ),
            email=child_text(contact_el, "ram:EmailURIUniversalCommunication/ram:URIID", NS),
        )
    addr_el = el.find("ram:PostalTradeAddress", NS)
    if addr_el is None:
        raise ParseError(f"missing PostalTradeAddress in {el.tag}")
    return Party(
        name=req_text(el, "ram:Name", NS),
        vat_id=child_text(el, "ram:SpecifiedTaxRegistration/ram:ID[@schemeID='VA']", NS),
        address=PostalAddress(
            line1=child_text(addr_el, "ram:LineOne", NS),
            city=child_text(addr_el, "ram:CityName", NS),
            post_code=child_text(addr_el, "ram:PostcodeCode", NS),
            country_code=req_text(addr_el, "ram:CountryID", NS),
        ),
        electronic_address=child_text(el, "ram:URIUniversalCommunication/ram:URIID", NS),
        contact=contact,
    )


def _dec(el: etree._Element, path: str) -> Decimal:
    return Decimal(req_text(el, path, NS))


def _opt_dec(el: etree._Element, path: str) -> Decimal | None:
    raw = child_text(el, path, NS)
    return None if raw is None else Decimal(raw)


def from_cii(data: bytes) -> Invoice:
    """Parse CII bytes into the canonical model. Raises ParseError on structural problems."""
    root = parse_xml(data)
    if root.tag != f"{{{RSM}}}CrossIndustryInvoice":
        raise ParseError(f"not a CII invoice: root is {root.tag}")
    doc = root.find("rsm:ExchangedDocument", NS)
    tx = root.find("rsm:SupplyChainTradeTransaction", NS)
    if doc is None or tx is None:
        raise ParseError("missing ExchangedDocument or SupplyChainTradeTransaction")
    agreement = tx.find("ram:ApplicableHeaderTradeAgreement", NS)
    settlement = tx.find("ram:ApplicableHeaderTradeSettlement", NS)
    if agreement is None or settlement is None:
        raise ParseError("missing header agreement or settlement")
    seller_el = agreement.find("ram:SellerTradeParty", NS)
    buyer_el = agreement.find("ram:BuyerTradeParty", NS)
    sums = settlement.find("ram:SpecifiedTradeSettlementHeaderMonetarySummation", NS)
    if seller_el is None or buyer_el is None or sums is None:
        raise ParseError("missing seller, buyer or monetary summation")

    lines = []
    for item in tx.findall("ram:IncludedSupplyChainTradeLineItem", NS):
        qty_el = item.find("ram:SpecifiedLineTradeDelivery/ram:BilledQuantity", NS)
        if qty_el is None or qty_el.text is None:
            raise ParseError("line without BilledQuantity")
        tax_path = "ram:SpecifiedLineTradeSettlement/ram:ApplicableTradeTax"
        lines.append(
            InvoiceLine(
                line_id=req_text(item, "ram:AssociatedDocumentLineDocument/ram:LineID", NS),
                quantity=Decimal(qty_el.text.strip()),
                unit_code=qty_el.get("unitCode", ""),
                net_price=_dec(
                    item,
                    "ram:SpecifiedLineTradeAgreement/ram:NetPriceProductTradePrice/ram:ChargeAmount",
                ),
                net_amount=_dec(
                    item,
                    "ram:SpecifiedLineTradeSettlement/"
                    "ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount",
                ),
                vat_category=req_text(item, f"{tax_path}/ram:CategoryCode", NS),
                vat_rate=_opt_dec(item, f"{tax_path}/ram:RateApplicablePercent"),
                description=child_text(item, "ram:SpecifiedTradeProduct/ram:Name", NS),
            )
        )

    breakdown = [
        VatBreakdown(
            taxable_amount=_dec(tax, "ram:BasisAmount"),
            tax_amount=_dec(tax, "ram:CalculatedAmount"),
            category=req_text(tax, "ram:CategoryCode", NS),
            rate=_opt_dec(tax, "ram:RateApplicablePercent"),
        )
        for tax in settlement.findall("ram:ApplicableTradeTax", NS)
    ]

    try:
        return Invoice(
            number=req_text(doc, "ram:ID", NS),
            issue_date=_required_date(doc, "ram:IssueDateTime"),
            type_code=req_text(doc, "ram:TypeCode", NS),
            currency=req_text(settlement, "ram:InvoiceCurrencyCode", NS),
            due_date=_parse_date(
                settlement.find("ram:SpecifiedTradePaymentTerms", NS), "ram:DueDateDateTime"
            ),
            buyer_reference=child_text(agreement, "ram:BuyerReference", NS),
            purchase_order_ref=child_text(
                agreement, "ram:BuyerOrderReferencedDocument/ram:IssuerAssignedID", NS
            ),
            seller=_parse_party(seller_el),
            seller_iban=child_text(
                settlement,
                "ram:SpecifiedTradeSettlementPaymentMeans/"
                "ram:PayeePartyCreditorFinancialAccount/ram:IBANID",
                NS,
            ),
            payment_means_code=child_text(
                settlement, "ram:SpecifiedTradeSettlementPaymentMeans/ram:TypeCode", NS
            )
            or "58",
            specification_id=child_text(
                root,
                "rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID",
                NS,
            ),
            buyer=_parse_party(buyer_el),
            lines=tuple(lines),
            vat_breakdown=tuple(breakdown),
            totals=Totals(
                line_net_sum=_dec(sums, "ram:LineTotalAmount"),
                total_without_vat=_dec(sums, "ram:TaxBasisTotalAmount"),
                vat_total=_dec(sums, "ram:TaxTotalAmount"),
                total_with_vat=_dec(sums, "ram:GrandTotalAmount"),
                amount_due=_dec(sums, "ram:DuePayableAmount"),
            ),
        )
    except ValueError as exc:  # pydantic ValidationError subclasses ValueError
        raise ParseError(f"invoice does not fit the canonical model: {exc}") from exc


def _required_date(el: etree._Element, path: str) -> dt.date:
    value = _parse_date(el, path)
    if value is None:
        raise ParseError(f"missing date at {path}")
    return value
