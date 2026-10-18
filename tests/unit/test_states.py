import itertools

import pytest

from rechnungsfluss.domain.states import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransitionError,
    InvoiceState,
    transition,
)

S = InvoiceState


def test_every_state_has_a_transition_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(InvoiceState)


def test_terminal_states() -> None:
    expected = {S.EXPORTED, S.REJECTED}
    assert expected == TERMINAL_STATES


@pytest.mark.parametrize(
    "path",
    [
        [S.RECEIVED, S.PARSED, S.VALIDATED, S.MATCHED, S.AUTO_APPROVED, S.EXPORTED],
        [S.RECEIVED, S.PARSED, S.VALIDATED, S.MATCHED, S.IN_REVIEW, S.APPROVED, S.EXPORTED],
        [S.RECEIVED, S.PARSED, S.VALIDATED, S.MATCHED, S.IN_REVIEW, S.REJECTED],
        [S.APPROVED, S.EXPORT_FAILED, S.EXPORTED],
        [S.RECEIVED, S.FAILED, S.RECEIVED],
    ],
)
def test_happy_and_replay_paths(path: list[InvoiceState]) -> None:
    for current, target in itertools.pairwise(path):
        assert transition(current, target) == target


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.RECEIVED, S.EXPORTED),
        (S.MATCHED, S.EXPORTED),  # must be approved first
        (S.IN_REVIEW, S.EXPORTED),
        (S.REJECTED, S.APPROVED),
        (S.EXPORTED, S.RECEIVED),
    ],
)
def test_forbidden_transitions(current: InvoiceState, target: InvoiceState) -> None:
    with pytest.raises(InvalidTransitionError):
        transition(current, target)


def test_export_only_after_an_approval() -> None:
    can_export = {s for s, nxt in ALLOWED_TRANSITIONS.items() if S.EXPORTED in nxt}
    assert can_export == {S.AUTO_APPROVED, S.APPROVED, S.EXPORT_FAILED}
