"""CI gate: the end-to-end local pipeline must route every labelled document correctly.

Zero false accepts is the hard requirement (ADR-0014). Full route and reason
agreement is required as well, because labels and rules are deterministic;
see eval/reports/*-routing.md for what this does and does not prove.
"""

from pathlib import Path

import pytest

from rechnungsfluss.evaluation.routing import evaluate
from rechnungsfluss.synthetic.generate import generate
from rechnungsfluss.validation.kosit import KositValidator

pytestmark = pytest.mark.kosit


def test_routing_has_no_false_accepts_and_matches_labels(
    validator: KositValidator, tmp_path: Path
) -> None:
    dataset, work = tmp_path / "dataset", tmp_path / "work"
    work.mkdir()
    generate(dataset)
    report = evaluate(dataset, validator, work)
    assert report.false_accepts == []
    assert report.route_agree == report.documents, report.disagreements
    assert report.reasons_agree == report.documents, report.disagreements
    assert report.duplicate_fp == report.duplicate_fn == 0
    assert report.audit_chains_verified == report.audit_chains_total > 0
