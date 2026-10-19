"""LLM extraction evaluation on the plain-PDF documents of the labelled dataset.

    # PR CI: replay recorded responses, no network, no cost (ADR-0010)
    python -m rechnungsfluss.evaluation.extraction --mode recorded --gate

    # Owner, with Bedrock access (runbook section 4): live calls, records cassettes
    python -m rechnungsfluss.evaluation.extraction --mode live --aws-profile <profile> --record

Reports (eval/reports/<date>-extraction-<mode>.{json,md}) contain only
measured values: field-level accuracy, wrong-but-unflagged fields ("silent
errors"), incomplete extractions, latency and token usage. A routing run with
the same extractor measures false accepts end to end.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import statistics
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.domain.tenancy import TenantId
from rechnungsfluss.evaluation.structured import _git_commit, _group, _pct
from rechnungsfluss.extraction.claude import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    PROMPT_VERSION,
    BedrockCall,
    ClaudeExtractor,
    LlmCall,
    RecordingCall,
    ReplayCall,
)

CASSETTES = Path("eval/cassettes")
BASELINE = Path("eval/baselines/extraction.json")
THRESHOLDS = Path("eval/thresholds.json")


def same(expected: object, got: object) -> bool:
    """Field equality: numbers by value (19 == 19.00), text by whitespace-normalized value."""
    if isinstance(expected, Decimal) and isinstance(got, Decimal):
        return expected == got
    if isinstance(expected, str) and isinstance(got, str):
        return " ".join(expected.split()) == " ".join(got.split())
    return expected == got


@dataclass
class FieldStats:
    compared: int = 0
    correct: int = 0


@dataclass
class ExtractionReport:
    generated_at: str
    git_commit: str
    mode: str
    model: str
    prompt_version: str
    effort: str
    dataset_manifest_sha256: str
    documents: int = 0
    extracted: int = 0
    incomplete: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    documents_fully_correct: int = 0
    fields: dict[str, FieldStats] = field(default_factory=dict)
    by_layout: dict[str, FieldStats] = field(default_factory=dict)
    silent_errors: list[dict[str, str]] = field(default_factory=list)
    """Wrong value, not listed in uncertain_fields: the dangerous kind."""
    flagged_errors: int = 0
    flagged_but_correct: int = 0
    latency_ms: list[int] = field(default_factory=list)
    input_tokens: list[int] = field(default_factory=list)
    output_tokens: list[int] = field(default_factory=list)

    def totals(self) -> FieldStats:
        t = FieldStats()
        for s in self.fields.values():
            t.compared += s.compared
            t.correct += s.correct
        return t

    @property
    def accuracy(self) -> float | None:
        t = self.totals()
        return t.correct / t.compared if t.compared else None


def _flagged(path: str, uncertain: tuple[str, ...]) -> bool:
    """The model may flag 'lines[0].quantity', 'lines[0]' or just 'lines'."""
    return any(path == u or path.startswith(u + ".") or path.startswith(u + "[") for u in uncertain)


def evaluate(
    dataset: Path, extractor: ClaudeExtractor, mode: str, limit: int | None = None
) -> ExtractionReport:
    manifest = dataset / "manifest.jsonl"
    report = ExtractionReport(
        generated_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        git_commit=_git_commit(),
        mode=mode,
        model=extractor.model,
        prompt_version=extractor.prompt_version,
        effort=extractor.effort,
        dataset_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
    )
    for line in manifest.read_text("utf-8").splitlines():
        label = json.loads(line)
        if label["format"] != "pdf" or label["truth"] is None:
            continue
        if label["category"] == "duplicate" and label["variant"] == "exact_resend":
            continue  # same bytes as its original: would be scored twice
        if limit is not None and report.documents >= limit:
            break
        report.documents += 1
        pdf = (dataset / label["file"]).read_bytes()
        try:
            result = extractor.extract(TenantId(label["tenant"]), pdf)
        except Exception as exc:
            report.errors.append(f"{label['doc_id']}: {type(exc).__name__}: {exc}")
            continue
        report.latency_ms.append(result.latency_ms or 0)
        report.input_tokens.append(int(result.usage.get("input_tokens", 0)))
        report.output_tokens.append(int(result.usage.get("output_tokens", 0)))
        if result.invoice is None:
            report.incomplete.append(
                f"{label['doc_id']}: missing {', '.join(result.missing_fields) or 'not an invoice'}"
            )
            continue
        report.extracted += 1
        truth = Invoice.model_validate(label["truth"]).field_paths()
        got = result.invoice.field_paths()
        layout = report.by_layout.setdefault(str(label["layout"]), FieldStats())
        all_correct = True
        for path, expected in truth.items():
            if path == "specification_id":
                continue  # BT-24 is not a printed field
            stats = report.fields.setdefault(_group(path), FieldStats())
            ok = same(expected, got.get(path))
            flagged = _flagged(path, result.uncertain_fields)
            stats.compared += 1
            layout.compared += 1
            stats.correct += int(ok)
            layout.correct += int(ok)
            all_correct &= ok
            if not ok and flagged:
                report.flagged_errors += 1
            elif not ok:
                report.silent_errors.append(
                    {
                        "doc_id": label["doc_id"],
                        "field": path,
                        "expected": str(expected),
                        "got": str(got.get(path)),
                    }
                )
            elif flagged:
                report.flagged_but_correct += 1
        report.documents_fully_correct += int(all_correct)
    return report


def _p(values: list[int], q: float) -> str:
    if not values:
        return "n/a"
    ordered = sorted(values)
    return str(ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))])


def to_markdown(r: ExtractionReport) -> str:
    t = r.totals()
    lines = [
        f"# LLM extraction evaluation ({r.mode}), {r.generated_at[:10]}",
        "",
        f"- Commit `{r.git_commit}`, dataset manifest sha256 `{r.dataset_manifest_sha256[:16]}…`",
        f"- Model `{r.model}`, prompt `{r.prompt_version}`, effort `{r.effort}`",
        f"- Mode `{r.mode}`: "
        + (
            "responses replayed from cassettes recorded in a live run"
            if r.mode == "recorded"
            else "live calls to Amazon Bedrock"
        ),
        f"- Scope: {r.documents} plain-PDF documents (byte-identical re-sends excluded). "
        "Synthetic data generated by this project: results do not predict real-world accuracy.",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Field-level accuracy | {_pct(r.accuracy)} ({t.correct} / {t.compared}) |",
        f"| Documents with every field correct | {r.documents_fully_correct} / {r.extracted} |",
        f"| **Silent errors** (wrong and not flagged uncertain) | {len(r.silent_errors)} |",
        f"| Wrong but flagged uncertain | {r.flagged_errors} |",
        f"| Flagged uncertain but correct | {r.flagged_but_correct} |",
        f"| Incomplete extractions (routed to review) | {len(r.incomplete)} |",
        f"| Errors | {len(r.errors)} |",
        f"| Latency p50 / p95 (ms) | {_p(r.latency_ms, 0.5)} / {_p(r.latency_ms, 0.95)} |",
        f"| Input / output tokens per document (mean) | "
        f"{round(statistics.mean(r.input_tokens)) if r.input_tokens else 'n/a'} / "
        f"{round(statistics.mean(r.output_tokens)) if r.output_tokens else 'n/a'} |",
        "",
        "## Accuracy by layout",
        "",
        "| Layout | Correct / compared | Accuracy |",
        "|---|---|---|",
    ]
    for name, s in sorted(r.by_layout.items()):
        lines.append(
            f"| {name} | {s.correct} / {s.compared} | "
            f"{_pct(s.correct / s.compared if s.compared else None)} |"
        )
    lines += [
        "",
        "## Accuracy by field",
        "",
        "| Field | Correct / compared | Accuracy |",
        "|---|---|---|",
    ]
    for name, s in sorted(r.fields.items()):
        lines.append(
            f"| `{name}` | {s.correct} / {s.compared} | "
            f"{_pct(s.correct / s.compared if s.compared else None)} |"
        )
    if r.silent_errors or r.incomplete or r.errors:
        lines += ["", "## Failures", ""]
        lines += [f"- silent error: {e}" for e in r.silent_errors[:60]]
        lines += [f"- incomplete: {e}" for e in r.incomplete]
        lines += [f"- error: {e}" for e in r.errors]
    lines.append("")
    return "\n".join(lines)


def gate(report: ExtractionReport, baseline_path: Path, thresholds_path: Path) -> list[str]:
    """CI gate (ADR-0010). Returns failure messages; empty means pass."""
    failures = []
    if report.errors:
        failures.append(f"{len(report.errors)} extraction errors (e.g. missing cassettes)")
    thresholds: dict[str, Any] = (
        json.loads(thresholds_path.read_text("utf-8")) if thresholds_path.is_file() else {}
    )
    minimum = thresholds.get("extraction_field_accuracy_min")
    if minimum is not None and (report.accuracy or 0) < float(minimum):
        failures.append(
            f"field accuracy {report.accuracy:.4f} below the owner's threshold {minimum}"
        )
    if baseline_path.is_file():
        baseline = json.loads(baseline_path.read_text("utf-8"))
        allowed = float(baseline["field_accuracy"]) - 0.02
        if (report.accuracy or 0) < allowed:
            failures.append(
                f"field accuracy {report.accuracy:.4f} dropped more than 2 pp below "
                f"the baseline {baseline['field_accuracy']:.4f}"
            )
        if len(report.silent_errors) > int(baseline["silent_errors"]):
            failures.append(
                f"silent errors rose from {baseline['silent_errors']} to "
                f"{len(report.silent_errors)}"
            )
    return failures


def _extractor(args: argparse.Namespace) -> ClaudeExtractor:
    call: LlmCall
    if args.mode == "recorded":
        call = ReplayCall(args.cassettes)
    else:
        call = BedrockCall(region=args.region, profile=args.aws_profile)
        if args.record:
            call = RecordingCall(call, args.cassettes)
    return ClaudeExtractor(
        call, model=args.model, prompt_version=args.prompt_version, effort=args.effort
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate LLM extraction on plain PDFs.")
    parser.add_argument("--mode", choices=["recorded", "live"], default="recorded")
    parser.add_argument("--dataset", type=Path, default=Path("eval/dataset"))
    parser.add_argument("--cassettes", type=Path, default=CASSETTES)
    parser.add_argument("--out", type=Path, default=Path("eval/reports"))
    parser.add_argument("--model", default=os.environ.get("RF_BEDROCK_MODEL_ID") or DEFAULT_MODEL)
    parser.add_argument("--prompt-version", default=PROMPT_VERSION)
    parser.add_argument(
        "--effort", default=DEFAULT_EFFORT, choices=["low", "medium", "high", "xhigh", "max"]
    )
    parser.add_argument("--region", default="eu-central-1")
    parser.add_argument("--aws-profile", default=os.environ.get("RF_BEDROCK_PROFILE") or None)
    parser.add_argument("--record", action="store_true", help="live mode: write cassettes")
    parser.add_argument(
        "--routing",
        action="store_true",
        help="also run end-to-end routing with this extractor (needs KoSIT)",
    )
    parser.add_argument("--kosit-url", default="http://127.0.0.1:8081")
    parser.add_argument("--gate", action="store_true", help="exit 1 if the CI gate fails")
    parser.add_argument(
        "--limit", type=int, default=None, help="evaluate only the first N documents (smoke test)"
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="store this run as eval/baselines/extraction.json",
    )
    args = parser.parse_args(argv)

    if args.mode == "recorded" and not any(args.cassettes.glob("*.json")):
        print(
            "No cassettes recorded yet: the first live run (runbook section 4) is pending. "
            "Nothing to evaluate; this is reported, not a pass of the accuracy gate."
        )
        return 0

    extractor = _extractor(args)
    if args.limit is not None and (args.write_baseline or args.gate):
        parser.error("--limit is for smoke tests; it cannot write a baseline or gate")
    report = evaluate(args.dataset, extractor, args.mode, args.limit)
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.out / f"{report.generated_at[:10]}-extraction-{args.mode}"
    payload = asdict(report) | {"field_accuracy": report.accuracy}
    stem.with_suffix(".json").write_text(
        json.dumps(payload, indent=1, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    stem.with_suffix(".md").write_text(to_markdown(report), encoding="utf-8", newline="\n")
    print(
        f"wrote {stem}.md: accuracy {_pct(report.accuracy)}, "
        f"silent errors {len(report.silent_errors)}"
    )

    if args.routing:
        from rechnungsfluss.evaluation import routing
        from rechnungsfluss.validation.kosit import KositValidator

        with tempfile.TemporaryDirectory() as tmp:
            rr = routing.evaluate(
                args.dataset,
                KositValidator(args.kosit_url, timeout_s=60),
                Path(tmp),
                extractor=extractor,
            )
        rstem = args.out / f"{rr.generated_at[:10]}-routing-llm-{args.mode}"
        rstem.with_suffix(".json").write_text(
            json.dumps(asdict(rr), indent=1, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        rstem.with_suffix(".md").write_text(routing.to_markdown(rr), encoding="utf-8", newline="\n")
        print(f"wrote {rstem}.md: false accepts {len(rr.false_accepts)}")
        if args.gate and rr.false_accepts:
            print(f"GATE FAILED: false accepts {rr.false_accepts}")
            return 1

    if args.write_baseline:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(
            json.dumps(
                {
                    "field_accuracy": report.accuracy,
                    "silent_errors": len(report.silent_errors),
                    "model": report.model,
                    "prompt_version": report.prompt_version,
                    "effort": report.effort,
                    "measured_at": report.generated_at,
                    "report": f"{stem.name}.md",
                },
                indent=1,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    if args.gate:
        failures = gate(report, BASELINE, THRESHOLDS)
        for failure in failures:
            print(f"GATE FAILED: {failure}")
        return 1 if failures else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
