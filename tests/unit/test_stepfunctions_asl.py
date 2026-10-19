"""The Step Functions definition must mirror the local pipeline (ADR-0009 mitigation).

The local runner and the state machine are two drivers of the same steps; this
test fails if they drift apart.
"""

from typing import Any

import pytest

from rechnungsfluss.application.pipeline import AUTOMATIC_STEPS, Step
from rechnungsfluss.domain.states import InvoiceState
from tests.terraform_templates import PIPELINE_MODULE, render

STEP_FN = "arn:aws:lambda:eu-central-1:000000000000:function:rf-dev-step"
REVIEW_FN = "arn:aws:lambda:eu-central-1:000000000000:function:rf-dev-await-review"


@pytest.fixture(scope="module")
def asl() -> dict[str, Any]:
    doc: dict[str, Any] = render(
        PIPELINE_MODULE / "asl.json.tftpl",
        {
            "step_function_arn": STEP_FN,
            "await_review_function_arn": REVIEW_FN,
            "review_timeout_seconds": 2592000,
        },
    )
    return doc


def _tasks(asl: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {n: s for n, s in asl["States"].items() if s["Type"] == "Task"}


def test_automatic_steps_run_in_pipeline_order(asl: dict[str, Any]) -> None:
    order, name = [], asl["StartAt"]
    while asl["States"][name]["Type"] == "Task":
        state = asl["States"][name]
        order.append(state["Parameters"]["Payload"]["step"])
        name = state["Next"]
    assert order == [s.value for s in AUTOMATIC_STEPS]
    assert asl["States"][name]["Type"] == "Choice"


def test_every_step_task_calls_the_step_function_with_a_known_step(asl: dict[str, Any]) -> None:
    for name, state in _tasks(asl).items():
        if name == "AwaitReview":
            continue
        assert state["Parameters"]["FunctionName"] == STEP_FN
        assert Step(state["Parameters"]["Payload"]["step"])


def test_every_task_retries_transient_errors_and_catches_the_rest(asl: dict[str, Any]) -> None:
    for name, state in _tasks(asl).items():
        retried = {e for r in state["Retry"] for e in r["ErrorEquals"]}
        assert "Lambda.TooManyRequestsException" in retried, name
        assert "ConflictError" in retried, name  # optimistic-concurrency race: just retry
        assert any("States.ALL" in c["ErrorEquals"] for c in state["Catch"]), name


def test_validator_outages_and_erp_failures_are_retried(asl: dict[str, Any]) -> None:
    validate = {e for r in asl["States"]["Validate"]["Retry"] for e in r["ErrorEquals"]}
    export = {e for r in asl["States"]["Export"]["Retry"] for e in r["ErrorEquals"]}
    assert "ValidatorUnavailableError" in validate
    assert "ExportFailedError" in export


def test_route_covers_every_state_decide_can_produce(asl: dict[str, Any]) -> None:
    routed = {c["StringEquals"] for c in asl["States"]["Route"]["Choices"]}
    assert {InvoiceState.AUTO_APPROVED, InvoiceState.IN_REVIEW, InvoiceState.FAILED} <= routed


def test_review_waits_for_a_task_token_with_a_timeout(asl: dict[str, Any]) -> None:
    review = asl["States"]["AwaitReview"]
    assert review["Resource"].endswith(".waitForTaskToken")
    assert review["Parameters"]["Payload"]["task_token.$"] == "$$.Task.Token"
    assert review["Parameters"]["FunctionName"] == REVIEW_FN
    assert review["TimeoutSeconds"] == 2592000
    assert review["ResultPath"] == "$.review"  # keeps tenant and invoice_id in the state


def test_every_transition_target_exists_and_every_state_is_reachable(asl: dict[str, Any]) -> None:
    states = asl["States"]
    edges: dict[str, set[str]] = {}
    for name, state in states.items():
        targets = {state["Next"]} if "Next" in state else set()
        targets |= {c["Next"] for c in state.get("Choices", [])}
        targets |= {c["Next"] for c in state.get("Catch", [])}
        if "Default" in state:
            targets.add(state["Default"])
        assert targets <= set(states), (name, targets - set(states))
        edges[name] = targets
    seen, todo = set(), [asl["StartAt"]]
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo.extend(edges[name])
    assert seen == set(states)


def test_terminal_states(asl: dict[str, Any]) -> None:
    terminal = {n for n, s in asl["States"].items() if s["Type"] in ("Succeed", "Fail")}
    assert {"Exported", "Rejected"} <= terminal
    assert {"InvoiceFailed", "ExportFailed", "ReviewTimedOut", "StepFailed"} <= terminal
