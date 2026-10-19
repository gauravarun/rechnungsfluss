"""Detect the syntax of an incoming document from its bytes (never from the file name)."""

from __future__ import annotations

from enum import StrEnum

from rechnungsfluss.formats import cii, ubl
from rechnungsfluss.formats.common import ParseError, parse_xml


class Syntax(StrEnum):
    CII = "cii"
    UBL = "ubl"
    PDF = "pdf"


def detect(data: bytes) -> Syntax:
    if data.lstrip()[:5] == b"%PDF-":
        return Syntax.PDF
    root = parse_xml(data)
    if root.tag == f"{{{cii.RSM}}}CrossIndustryInvoice":
        return Syntax.CII
    if root.tag == f"{{{ubl.INV}}}Invoice":
        return Syntax.UBL
    raise ParseError(f"unsupported document: root element {root.tag}")
