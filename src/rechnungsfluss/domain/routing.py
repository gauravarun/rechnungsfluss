"""Routing decision: auto-approve only when nothing at all needs a human (ADR-0014)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from rechnungsfluss.domain.duplicates import DuplicateHit
from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.domain.matching import MatchIssue
from rechnungsfluss.domain.reasons import Reason


class Route(StrEnum):
    AUTO_APPROVE = "auto_approve"
    REVIEW = "review"


@dataclass(frozen=True, slots=True)
class Decision:
    route: Route
    reasons: tuple[Reason, ...]


def decide(
    *,
    invoice: Invoice | None,
    is_e_invoice: bool,
    validation_accepted: bool | None,
    match_issues: Sequence[MatchIssue],
    duplicates: Sequence[DuplicateHit],
) -> Decision:
    """Combine every signal into one route.

    ``invoice`` is None when the document carries no usable invoice data (for
    example a Factur-X MINIMUM profile); ``validation_accepted`` is None when
    validation could not run for that reason.
    """
    reasons: set[Reason] = set()
    if not is_e_invoice:
        reasons.add(Reason.NOT_AN_E_INVOICE)
    if validation_accepted is False:
        reasons.add(Reason.VALIDATION_FAILED)
    if invoice is not None and invoice.due_date is None:
        # Our rule: EN 16931 BR-CO-25 is not enforced by the pinned validator (standards.md).
        reasons.add(Reason.MISSING_DUE_DATE)
    if duplicates:
        reasons.add(Reason.DUPLICATE_SUSPECTED)
    reasons.update(issue.reason for issue in match_issues)
    ordered = tuple(sorted(reasons, key=lambda r: r.value))
    return Decision(route=Route.REVIEW if ordered else Route.AUTO_APPROVE, reasons=ordered)
