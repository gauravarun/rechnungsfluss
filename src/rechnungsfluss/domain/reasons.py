"""Review reason codes. These are *our* business reasons, not EN 16931 rule IDs.

Official validation findings are reported separately with their rule IDs; the
reason ``validation_failed`` only says that the official validator rejected
the document.
"""

from __future__ import annotations

from enum import StrEnum


class Reason(StrEnum):
    VALIDATION_FAILED = "validation_failed"
    NOT_AN_INVOICE = "not_an_invoice"
    """A plain PDF that the extractor says is not an invoice."""
    EXTRACTION_INCOMPLETE = "extraction_incomplete"
    """Required data could not be read from a plain PDF."""
    EXTRACTION_UNCERTAIN = "extraction_uncertain"
    """The extractor flagged fields it could not read with confidence."""
    NOT_AN_E_INVOICE = "not_an_e_invoice"
    MISSING_DUE_DATE = "missing_due_date"
    DUPLICATE_SUSPECTED = "duplicate_suspected"
    NO_PO_REFERENCE = "no_po_reference"
    PO_NOT_FOUND = "po_not_found"
    PO_CURRENCY_MISMATCH = "po_currency_mismatch"
    PO_SUPPLIER_MISMATCH = "po_supplier_mismatch"
    PO_LINE_NOT_FOUND = "po_line_not_found"
    PO_UNIT_MISMATCH = "po_unit_mismatch"
    PO_PRICE_MISMATCH = "po_price_mismatch"
    PO_QUANTITY_MISMATCH = "po_quantity_mismatch"
