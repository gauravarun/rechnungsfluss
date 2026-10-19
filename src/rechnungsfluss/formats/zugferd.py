"""ZUGFeRD 2.x / Factur-X hybrid PDFs (PDF/A-3 with embedded CII).

The ``factur-x`` library (pinned in docs/standards.md) does profile detection,
XSD validation and embedding. We extract the attachment ourselves with pypdf
and run it through the hardened XML parser first, because factur-x parses
attachments with lxml's default parser. EN 16931 business rules are checked
afterwards by KoSIT, like every other input (ADR-0002).
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from enum import StrEnum

import facturx
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.formats import SPEC_EN16931, cii
from rechnungsfluss.formats.common import ParseError, parse_xml

MAX_PDF_BYTES = 20 * 1024 * 1024

# factur-x's known names, minus Order-X, plus ZUGFeRD 2.x's XRECHNUNG-profile name.
INVOICE_ATTACHMENT_NAMES = (
    "factur-x.xml",
    "zugferd-invoice.xml",
    "ZUGFeRD-invoice.xml",
    "xrechnung.xml",
)


class Profile(StrEnum):
    """Factur-X / ZUGFeRD 2.x profile, as named by the factur-x library."""

    MINIMUM = "minimum"
    BASIC_WL = "basicwl"
    BASIC = "basic"
    EN16931 = "en16931"
    EXTENDED = "extended"
    XRECHNUNG = "xrechnung"


# Profiles that do not carry a full EN 16931 invoice. Per the BMF letter cited
# (⚠️ unverified) in docs/standards.md they do not qualify as e-invoices, so the
# pipeline routes them to review instead of rejecting or auto-approving them.
NOT_AN_E_INVOICE = frozenset({Profile.MINIMUM, Profile.BASIC_WL})


class NotZugferdError(ParseError):
    """The PDF carries no Factur-X/ZUGFeRD invoice attachment (it may be a plain PDF)."""


@dataclass(frozen=True, slots=True)
class ZugferdDocument:
    xml: bytes
    attachment_name: str
    profile: Profile
    invoice: Invoice | None
    """None for profiles in NOT_AN_E_INVOICE: they lack the data the model requires."""

    @property
    def is_e_invoice(self) -> bool:
        return self.profile not in NOT_AN_E_INVOICE


def attachment(pdf: bytes) -> tuple[str, bytes]:
    """Return ``(name, raw bytes)`` of the invoice XML attachment, unparsed."""
    if len(pdf) > MAX_PDF_BYTES:
        raise ParseError(f"PDF larger than {MAX_PDF_BYTES} bytes")
    try:
        attachments = PdfReader(io.BytesIO(pdf)).attachments
    except (PdfReadError, ValueError) as exc:
        raise ParseError(f"unreadable PDF: {exc}") from exc
    for name in INVOICE_ATTACHMENT_NAMES:
        contents = attachments.get(name)
        if contents:
            return name, contents[0]
    raise NotZugferdError("no Factur-X/ZUGFeRD XML attachment found")


def _profile(root: object) -> Profile:
    level = facturx.get_level(root, flavor="factur-x")
    try:
        return Profile(level)
    except ValueError as exc:
        raise ParseError(f"unsupported Factur-X/ZUGFeRD profile {level!r}") from exc


def extract(pdf: bytes) -> ZugferdDocument:
    name, xml = attachment(pdf)
    root = parse_xml(xml)  # hardened: rejects DTD/entities before factur-x sees the bytes
    profile = Profile.XRECHNUNG if name == "xrechnung.xml" else _profile(root)
    if profile is not Profile.XRECHNUNG:
        try:
            facturx.xml_check_xsd(xml, flavor="factur-x", level=profile.value)
        except Exception as exc:  # factur-x raises generic exceptions on XSD failure
            raise ParseError(f"Factur-X XSD check failed ({profile}): {exc}") from exc
    invoice = cii.from_cii(xml) if profile not in NOT_AN_E_INVOICE else None
    return ZugferdDocument(xml=xml, attachment_name=name, profile=profile, invoice=invoice)


def embed(visual_pdf: bytes, invoice: Invoice) -> bytes:
    """Attach ``invoice`` as EN 16931-profile Factur-X XML to ``visual_pdf``."""
    xml = cii.to_cii(invoice, specification_id=SPEC_EN16931)
    result: bytes = facturx.generate_from_binary(
        visual_pdf,
        xml,
        flavor="factur-x",
        level="en16931",
        check_xsd=True,
        lang="de-DE",
        pdf_metadata={
            "author": invoice.seller.name,
            "keywords": "Factur-X, ZUGFeRD, synthetic",
            "title": f"Rechnung {invoice.number}",
            "subject": f"Rechnung {invoice.number} (synthetic)",
        },
    )
    return result
