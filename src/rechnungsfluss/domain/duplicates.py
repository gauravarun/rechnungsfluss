"""Business duplicate detection (ADR-0008). Our own rule, not an EN 16931 rule.

Technical re-deliveries of identical bytes are handled earlier by ingest
idempotency (ADR-0007); this module looks for the *same invoice* arriving as a
different document (another format, a re-issued reminder, a re-keyed number).
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.domain.matching import normalize_text
from rechnungsfluss.domain.tenant_config import DuplicateTolerances

_RUNS = re.compile(r"[a-z]+|[0-9]+")


class Fingerprint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    invoice_id: str
    seller_key: str
    number_key: str
    gross: Decimal
    issue_date: dt.date


class DuplicateHit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    original_invoice_id: str
    kind: str
    """``exact`` (same seller + normalized number) or ``fuzzy`` (amount and date)."""


def number_key(number: str) -> str:
    """Normalize case, separators and leading zeros per numeric run.

    'RE-2026-0042' == 're 2026 42' == 'RE2026/42'; 'RE-2026-1001' != 'RE-2026-11';
    'RE-20-26' != 'RE-2026' (run boundaries are kept).
    """
    runs = _RUNS.findall(number.casefold())
    return "-".join(r.lstrip("0") or "0" if r.isdigit() else r for r in runs)


def seller_key(invoice: Invoice) -> str:
    vat = invoice.seller.vat_id
    return f"vat:{vat}" if vat else f"name:{normalize_text(invoice.seller.name)}"


def fingerprint(invoice_id: str, invoice: Invoice) -> Fingerprint:
    return Fingerprint(
        invoice_id=invoice_id,
        seller_key=seller_key(invoice),
        number_key=number_key(invoice.number),
        gross=invoice.totals.total_with_vat,
        issue_date=invoice.issue_date,
    )


def find_duplicates(
    candidate: Fingerprint, earlier: Iterable[Fingerprint], tol: DuplicateTolerances
) -> list[DuplicateHit]:
    hits = []
    for other in earlier:
        if other.invoice_id == candidate.invoice_id or other.seller_key != candidate.seller_key:
            continue
        if other.number_key == candidate.number_key:
            hits.append(DuplicateHit(original_invoice_id=other.invoice_id, kind="exact"))
        elif (
            abs(other.gross - candidate.gross) <= tol.amount_abs
            and abs(other.issue_date - candidate.issue_date) <= tol.date_window
        ):
            hits.append(DuplicateHit(original_invoice_id=other.invoice_id, kind="fuzzy"))
    return hits
