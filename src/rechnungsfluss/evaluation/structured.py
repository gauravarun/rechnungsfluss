"""Evaluate the structured (XML) input path against the labelled dataset.

    python -m rechnungsfluss.evaluation.structured --dataset eval/dataset --out eval/reports

Measures, for CII, UBL and ZUGFeRD documents only:

- parse success (and correct "not an e-invoice" detection for MINIMUM profiles)
- field-level accuracy: every canonical field compared with the label's truth
- validation agreement: KoSIT verdict and rule-ID set vs. the label

Routing, PO matching and duplicate detection are not evaluated here (W3), and
neither is LLM extraction from plain PDFs (W4).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.formats import cii, ubl, zugferd
from rechnungsfluss.formats.common import ParseError
from rechnungsfluss.validation.kosit import KositValidator

STRUCTURED = ("cii", "ubl", "zugferd")


@dataclass
class FieldStats:
    compared: int = 0
    correct: int = 0

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.compared if self.compared else None


@dataclass
class Report:
    generated_at: str
    git_commit: str
    dataset_manifest_sha256: str
    validator: str
    documents: int = 0
    by_format: dict[str, int] = field(default_factory=dict)
    parse_ok: int = 0
    parse_failed: list[str] = field(default_factory=list)
    not_e_invoice_expected: int = 0
    not_e_invoice_detected: int = 0
    fields: dict[str, FieldStats] = field(default_factory=dict)
    field_errors: list[dict[str, str]] = field(default_factory=list)
    validation_compared: int = 0
    validation_agree: int = 0
    validation_disagreements: list[dict[str, object]] = field(default_factory=list)

    def field_totals(self) -> FieldStats:
        total = FieldStats()
        for stats in self.fields.values():
            total.compared += stats.compared
            total.correct += stats.correct
        return total


def _group(path: str) -> str:
    """Collapse list indices for per-field reporting: lines[3].quantity -> lines[].quantity."""
    out, depth = [], 0
    for ch in path:
        if ch == "[":
            depth += 1
            out.append("[]")
        elif ch == "]":
            depth -= 1
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _parse(fmt: str, data: bytes) -> tuple[Invoice | None, bytes, bool]:
    """Return (invoice, xml, is_e_invoice)."""
    if fmt == "cii":
        return cii.from_cii(data), data, True
    if fmt == "ubl":
        return ubl.from_ubl(data), data, True
    doc = zugferd.extract(data)
    return doc.invoice, doc.xml, doc.is_e_invoice


def evaluate(dataset: Path, validator: KositValidator, validator_name: str) -> Report:
    manifest = dataset / "manifest.jsonl"
    report = Report(
        generated_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        git_commit=_git_commit(),
        dataset_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        validator=validator_name,
    )
    formats: Counter[str] = Counter()
    for line in manifest.read_text("utf-8").splitlines():
        label = json.loads(line)
        if label["format"] not in STRUCTURED:
            continue
        report.documents += 1
        formats[label["format"]] += 1
        data = (dataset / label["file"]).read_bytes()
        try:
            invoice, xml, is_e_invoice = _parse(label["format"], data)
        except ParseError as exc:
            report.parse_failed.append(f"{label['doc_id']}: {exc}")
            continue
        report.parse_ok += 1

        if label["expected_validation"] == "not_an_e_invoice":
            report.not_e_invoice_expected += 1
            report.not_e_invoice_detected += int(not is_e_invoice)
            continue

        if invoice is not None and label["truth"] is not None:
            truth = Invoice.model_validate(label["truth"]).field_paths()
            got = invoice.field_paths()
            for path, expected in truth.items():
                stats = report.fields.setdefault(_group(path), FieldStats())
                stats.compared += 1
                if got.get(path) == expected:
                    stats.correct += 1
                elif len(report.field_errors) < 50:
                    report.field_errors.append(
                        {
                            "doc_id": label["doc_id"],
                            "field": path,
                            "expected": str(expected),
                            "got": str(got.get(path)),
                        }
                    )

        result = validator.validate(xml)
        observed = ("accept" if result.accepted else "reject", sorted(result.error_codes))
        expected_v = (label["expected_validation"], sorted(label["expected_rule_ids"]))
        report.validation_compared += 1
        if observed == expected_v:
            report.validation_agree += 1
        else:
            report.validation_disagreements.append(
                {"doc_id": label["doc_id"], "observed": observed, "expected": expected_v}
            )
    report.by_format = dict(sorted(formats.items()))
    return report


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.2f} %"


def to_markdown(r: Report) -> str:
    total = r.field_totals()
    formats = ", ".join(f"{k} {v}" for k, v in r.by_format.items())
    minimum = f"{r.not_e_invoice_detected} / {r.not_e_invoice_expected}"
    accuracy = f"{_pct(total.accuracy)} ({total.correct} / {total.compared})"
    agreement = f"{r.validation_agree} / {r.validation_compared}"
    lines = [
        f"# Structured-path evaluation, {r.generated_at[:10]}",
        "",
        f"- Commit `{r.git_commit}`, dataset manifest sha256 `{r.dataset_manifest_sha256[:16]}…`",
        f"- Validator: {r.validator}",
        f"- Scope: {r.documents} structured documents ({formats}). "
        "Plain PDFs, routing, PO matching and duplicates are **not** evaluated here.",
        "- Data: synthetic and generated by this project. "
        "Results do not predict real-world accuracy.",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Parsed without error | {r.parse_ok} / {r.documents} |",
        f"| MINIMUM profile detected as not an e-invoice | {minimum} |",
        f"| Field-level accuracy (all fields) | {accuracy} |",
        f"| Validation agreement with labels (verdict + exact rule IDs) | {agreement} |",
        "",
        "## Field accuracy by field",
        "",
        "| Field | Correct / compared | Accuracy |",
        "|---|---|---|",
    ]
    for name, stats in sorted(r.fields.items()):
        lines.append(f"| `{name}` | {stats.correct} / {stats.compared} | {_pct(stats.accuracy)} |")
    if r.parse_failed or r.field_errors or r.validation_disagreements:
        lines += ["", "## Failures", ""]
        lines += [f"- parse: {p}" for p in r.parse_failed]
        lines += [f"- field: {e}" for e in r.field_errors]
        lines += [f"- validation: {d}" for d in r.validation_disagreements]
    lines += [
        "",
        "## Reading this report",
        "",
        "Structured parsing is deterministic. 100 % here shows that the parsers agree with the "
        "generator that produced the data, which also uses this project's serializers. It is a "
        "regression check, **not** evidence of accuracy on real supplier XML. The validation "
        "figures come from the official KoSIT validator and are independent of our code.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate the structured XML path.")
    parser.add_argument("--dataset", type=Path, default=Path("eval/dataset"))
    parser.add_argument("--out", type=Path, default=Path("eval/reports"))
    parser.add_argument("--kosit-url", default="http://127.0.0.1:8081")
    parser.add_argument(
        "--validator-name",
        default="KoSIT validator 1.6.3 + XRechnung configuration v2026-08-31",
    )
    args = parser.parse_args(argv)
    report = evaluate(
        args.dataset, KositValidator(args.kosit_url, timeout_s=60), args.validator_name
    )
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.out / f"{report.generated_at[:10]}-structured"
    payload = asdict(report)
    payload["field_accuracy_total"] = report.field_totals().accuracy
    # newline="\n": byte-identical reports on every OS (no CRLF on Windows).
    stem.with_suffix(".json").write_text(
        json.dumps(payload, indent=1, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    stem.with_suffix(".md").write_text(to_markdown(report), encoding="utf-8", newline="\n")
    print(f"wrote {stem}.json and {stem}.md")  # noqa: T201


if __name__ == "__main__":
    main()
