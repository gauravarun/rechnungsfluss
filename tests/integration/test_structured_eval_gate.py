"""CI gate for the structured (XML) path.

Structured parsing is deterministic, so any drop below 100 % field accuracy,
or any disagreement with the KoSIT-verified labels, is a regression. The
threshold for the LLM path (W4) is set by the project owner after the first
baseline (ADR-0010); this gate covers only CII, UBL and ZUGFeRD.
"""

from pathlib import Path

import pytest

from rechnungsfluss.evaluation.structured import evaluate
from rechnungsfluss.synthetic.generate import generate
from rechnungsfluss.validation.kosit import KositValidator

pytestmark = pytest.mark.kosit


def test_structured_path_has_no_regressions(validator: KositValidator, tmp_path: Path) -> None:
    generate(tmp_path)
    report = evaluate(tmp_path, validator, "test")
    assert report.documents >= 100
    assert report.parse_failed == []
    assert report.not_e_invoice_detected == report.not_e_invoice_expected > 0
    assert report.field_totals().accuracy == 1.0, report.field_errors[:5]
    assert report.validation_agree == report.validation_compared, report.validation_disagreements
