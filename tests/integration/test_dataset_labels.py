"""Every label's validation expectation must equal what the official validator says.

Exact match: same verdict and the *same set* of error rule IDs, so a label can
never claim a rule the validator does not report (or miss one it does).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.formats import SPEC_EN16931, cii, zugferd
from rechnungsfluss.synthetic.generate import generate
from rechnungsfluss.validation.kosit import KositValidator

pytestmark = pytest.mark.kosit


def _observed(
    validator: KositValidator, label: dict[str, object], data: bytes
) -> tuple[str, set[str]]:
    fmt = label["format"]
    if fmt in ("cii", "ubl"):
        xml = data
    elif fmt == "zugferd":
        doc = zugferd.extract(data)
        if not doc.is_e_invoice:
            return "not_an_e_invoice", set()
        xml = doc.xml
    else:  # plain PDF: what a perfect extraction would be validated as (ADR-0002)
        truth = label["truth"]
        assert isinstance(truth, dict)
        xml = cii.to_cii(Invoice.model_validate(truth), specification_id=SPEC_EN16931)
    result = validator.validate(xml)
    return ("accept" if result.accepted else "reject"), set(result.error_codes)


def test_all_labels_match_kosit(validator: KositValidator, tmp_path: Path) -> None:
    generate(tmp_path)
    mismatches = []
    for line in (tmp_path / "manifest.jsonl").read_text("utf-8").splitlines():
        label = json.loads(line)
        observed = _observed(validator, label, (tmp_path / label["file"]).read_bytes())
        expected = (label["expected_validation"], set(label["expected_rule_ids"]))
        if observed != expected:
            mismatches.append((label["doc_id"], label["variant"], observed, expected))
    assert mismatches == []
