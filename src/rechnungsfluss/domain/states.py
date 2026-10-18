"""Invoice processing states and the transitions allowed between them."""

from __future__ import annotations

from enum import StrEnum


class InvoiceState(StrEnum):
    RECEIVED = "RECEIVED"
    PARSED = "PARSED"
    VALIDATED = "VALIDATED"
    MATCHED = "MATCHED"
    AUTO_APPROVED = "AUTO_APPROVED"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPORTED = "EXPORTED"
    EXPORT_FAILED = "EXPORT_FAILED"
    FAILED = "FAILED"


S = InvoiceState

ALLOWED_TRANSITIONS: dict[InvoiceState, frozenset[InvoiceState]] = {
    S.RECEIVED: frozenset({S.PARSED, S.FAILED}),
    S.PARSED: frozenset({S.VALIDATED, S.FAILED}),
    S.VALIDATED: frozenset({S.MATCHED, S.FAILED}),
    S.MATCHED: frozenset({S.AUTO_APPROVED, S.IN_REVIEW, S.FAILED}),
    S.AUTO_APPROVED: frozenset({S.EXPORTED, S.EXPORT_FAILED}),
    S.IN_REVIEW: frozenset({S.APPROVED, S.REJECTED}),
    S.APPROVED: frozenset({S.EXPORTED, S.EXPORT_FAILED}),
    S.EXPORT_FAILED: frozenset({S.EXPORTED, S.EXPORT_FAILED}),
    S.FAILED: frozenset({S.RECEIVED}),
    S.REJECTED: frozenset(),
    S.EXPORTED: frozenset(),
}

TERMINAL_STATES = frozenset(s for s, nxt in ALLOWED_TRANSITIONS.items() if not nxt)


class InvalidTransitionError(ValueError):
    def __init__(self, current: InvoiceState, target: InvoiceState) -> None:
        super().__init__(f"transition {current} -> {target} is not allowed")
        self.current = current
        self.target = target


def transition(current: InvoiceState, target: InvoiceState) -> InvoiceState:
    """Return ``target`` if the move is allowed, otherwise raise."""
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(current, target)
    return target
