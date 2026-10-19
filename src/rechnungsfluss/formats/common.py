"""Shared XML helpers. Parsing is hardened against XXE and entity expansion."""

from __future__ import annotations

from decimal import Decimal

from lxml import etree


class ParseError(ValueError):
    """The document could not be read into the canonical model."""


# No network access, no DTD loading, no entity resolution: invoices come from
# untrusted suppliers (see docs/security.md, once written in W6).
_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    huge_tree=False,
    remove_blank_text=True,
)

MAX_XML_BYTES = 10 * 1024 * 1024


def parse_xml(data: bytes) -> etree._Element:
    if len(data) > MAX_XML_BYTES:
        raise ParseError(f"XML larger than {MAX_XML_BYTES} bytes")
    if b"<!DOCTYPE" in data[:4096] or b"<!ENTITY" in data:
        raise ParseError("DOCTYPE/ENTITY declarations are not accepted")
    try:
        return etree.fromstring(data, parser=_PARSER)
    except etree.XMLSyntaxError as exc:
        raise ParseError(f"malformed XML: {exc}") from exc


def child_text(el: etree._Element, path: str, ns: dict[str, str]) -> str | None:
    found = el.find(path, ns)
    if found is None or found.text is None:
        return None
    text = found.text.strip()
    return text or None


def req_text(el: etree._Element, path: str, ns: dict[str, str]) -> str:
    value = child_text(el, path, ns)
    if value is None:
        raise ParseError(f"missing required element {path}")
    return value


def fmt_amount(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):f}"


def fmt_quantity(value: Decimal) -> str:
    text = f"{value.normalize():f}"
    return text
