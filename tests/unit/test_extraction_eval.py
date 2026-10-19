"""The extraction harness itself, driven by a simulated model (no network)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.evaluation.extraction import evaluate, gate, same, to_markdown
from rechnungsfluss.extraction.claude import ClaudeExtractor, LlmRequest, LlmResponse
from rechnungsfluss.synthetic.generate import generate


def as_model_output(inv: Invoice) -> dict[str, Any]:
    """What a perfect model would return for ``inv`` (inverse of extraction/mapping.py)."""

    def party(p: Any) -> dict[str, Any]:
        a = p.address
        return {
            "name": p.name,
            "vat_id": p.vat_id,
            "email": p.electronic_address,
            "address": {
                "line1": a.line1,
                "city": a.city,
                "post_code": a.post_code,
                "country_code": a.country_code,
            },
        }

    c = inv.seller.contact
    return {
        "is_invoice": True,
        "number": inv.number,
        "issue_date": inv.issue_date.isoformat(),
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "currency": inv.currency,
        "purchase_order_ref": inv.purchase_order_ref,
        "buyer_reference": inv.buyer_reference,
        "seller": party(inv.seller),
        "seller_contact": {"name": c.name, "phone": c.phone, "email": c.email} if c else None,
        "seller_iban": inv.seller_iban,
        "buyer": party(inv.buyer),
        "lines": [
            {
                "position": ln.line_id,
                "description": ln.description,
                "quantity": f"{ln.quantity:f}",
                "unit_code": ln.unit_code,
                "net_price": f"{ln.net_price:f}",
                "net_amount": f"{ln.net_amount:f}",
                "vat_rate_percent": f"{ln.vat_rate:f}" if ln.vat_rate is not None else None,
                "order_line_ref": ln.order_line_ref,
            }
            for ln in inv.lines
        ],
        "vat_breakdown": [
            {
                "rate_percent": f"{v.rate:f}" if v.rate is not None else None,
                "taxable_amount": f"{v.taxable_amount:f}",
                "tax_amount": f"{v.tax_amount:f}",
            }
            for v in inv.vat_breakdown
        ],
        "totals": {
            "line_net_sum": f"{inv.totals.line_net_sum:f}",
            "total_without_vat": f"{inv.totals.total_without_vat:f}",
            "vat_total": f"{inv.totals.vat_total:f}",
            "total_with_vat": f"{inv.totals.total_with_vat:f}",
            "amount_due": f"{inv.totals.amount_due:f}",
        },
        "uncertain_fields": [],
    }


class SimulatedModel:
    """Perfect reader, optionally misreading one document's due date."""

    def __init__(self, dataset: Path, misread: str | None = None, flag: bool = False) -> None:
        import hashlib

        self.by_sha: dict[str, tuple[str, Invoice]] = {}
        for line in (dataset / "manifest.jsonl").read_text("utf-8").splitlines():
            lbl = json.loads(line)
            if lbl["format"] == "pdf" and lbl["truth"]:
                data = (dataset / lbl["file"]).read_bytes()
                self.by_sha[hashlib.sha256(data).hexdigest()] = (
                    lbl["doc_id"],
                    Invoice.model_validate(lbl["truth"]),
                )
        self.misread, self.flag = misread, flag

    def __call__(self, request: LlmRequest) -> LlmResponse:
        import hashlib

        doc_id, inv = self.by_sha[hashlib.sha256(request.pdf).hexdigest()]
        output = as_model_output(inv)
        if doc_id == self.misread:
            output["due_date"] = "2099-01-01"
            if self.flag:
                output["uncertain_fields"] = ["due_date"]
        return LlmResponse(
            output=output,
            model=request.model,
            stop_reason="end_turn",
            usage={"input_tokens": 2500, "output_tokens": 800},
            latency_ms=3000,
        )


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("dataset")
    generate(out)
    return out


def _first_pdf(dataset: Path) -> str:
    for line in (dataset / "manifest.jsonl").read_text("utf-8").splitlines():
        lbl = json.loads(line)
        if lbl["format"] == "pdf" and lbl["truth"] and lbl["truth"]["due_date"]:
            return str(lbl["doc_id"])
    raise AssertionError("no plain PDF with a due date")


def test_perfect_reader_scores_100_percent(dataset: Path) -> None:
    report = evaluate(dataset, ClaudeExtractor(SimulatedModel(dataset)), "recorded")
    assert report.documents >= 25
    assert report.extracted == report.documents
    assert report.accuracy == 1.0
    assert report.silent_errors == []
    assert set(report.by_layout) == {"classic", "compact", "scan"}
    assert "100.00 %" in to_markdown(report)


def test_unflagged_misread_is_a_silent_error(dataset: Path) -> None:
    doc = _first_pdf(dataset)
    report = evaluate(dataset, ClaudeExtractor(SimulatedModel(dataset, misread=doc)), "recorded")
    assert [(e["doc_id"], e["field"]) for e in report.silent_errors] == [(doc, "due_date")]
    assert report.accuracy is not None
    assert report.accuracy < 1.0


def test_flagged_misread_is_not_silent(dataset: Path) -> None:
    doc = _first_pdf(dataset)
    report = evaluate(dataset, ClaudeExtractor(SimulatedModel(dataset, doc, flag=True)), "recorded")
    assert report.silent_errors == []
    assert report.flagged_errors == 1


def test_gate(tmp_path: Path, dataset: Path) -> None:
    report = evaluate(
        dataset, ClaudeExtractor(SimulatedModel(dataset, _first_pdf(dataset))), "recorded"
    )
    baseline, thresholds = tmp_path / "b.json", tmp_path / "t.json"
    assert gate(report, baseline, thresholds) == []  # nothing set yet: only errors gate
    baseline.write_text(json.dumps({"field_accuracy": 1.0, "silent_errors": 0}))
    assert any("silent errors rose" in f for f in gate(report, baseline, thresholds))
    thresholds.write_text(json.dumps({"extraction_field_accuracy_min": 0.9999}))
    assert any("owner's threshold" in f for f in gate(report, baseline, thresholds))


@pytest.mark.parametrize(
    ("a", "b", "equal"),
    [
        (Decimal("19"), Decimal("19.00"), True),
        ("Muster  KG ", "Muster KG", True),
        ("DE1", "DE2", False),
        (None, None, True),
        (Decimal("1"), None, False),
    ],
)
def test_same(a: object, b: object, equal: bool) -> None:
    assert same(a, b) is equal
