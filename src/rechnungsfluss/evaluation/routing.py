"""End-to-end routing evaluation: the local pipeline over the labelled dataset.

    python -m rechnungsfluss.evaluation.routing --dataset eval/dataset --out eval/reports

Every document is ingested in manifest order (originals before their
duplicates) and processed by the real pipeline with the real KoSIT validator.
Plain PDFs use an **oracle extractor** that returns the label's ground truth,
so this report measures everything *after* extraction (validation, duplicate
detection, 2-way matching, routing, export), not extraction itself (W4).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rechnungsfluss.adapters.local import (
    InMemoryErp,
    LocalObjectStore,
    SqliteAuditLog,
    SqliteFingerprintStore,
    SqliteIdempotencyStore,
    SqliteInvoiceRepository,
    SqlitePurchaseOrderRepository,
    connect,
    utc_now,
)
from rechnungsfluss.application.pipeline import Pipeline, Services
from rechnungsfluss.domain.audit import verify_chain
from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.domain.matching import PurchaseOrder
from rechnungsfluss.domain.states import InvoiceState
from rechnungsfluss.domain.tenancy import TenantId
from rechnungsfluss.evaluation.structured import _git_commit
from rechnungsfluss.ports import Validator

S = InvoiceState


class OracleExtractor:
    """Returns the labelled truth for a PDF. Evaluation only; never used in production."""

    def __init__(self, truths: dict[str, Invoice]) -> None:
        self._by_sha = truths

    def extract(self, tenant: TenantId, pdf: bytes) -> Invoice:
        return self._by_sha[hashlib.sha256(pdf).hexdigest()]


@dataclass
class RoutingReport:
    generated_at: str
    git_commit: str
    dataset_manifest_sha256: str
    documents: int = 0
    route_agree: int = 0
    reasons_agree: int = 0
    false_accepts: list[str] = field(default_factory=list)
    """Label says review (or skip), pipeline auto-approved: the key safety metric."""
    false_reviews: list[str] = field(default_factory=list)
    disagreements: list[dict[str, object]] = field(default_factory=list)
    expected_review: int = 0
    observed_review: int = 0
    exported: int = 0
    failed: int = 0
    duplicate_tp: int = 0
    duplicate_fp: int = 0
    duplicate_fn: int = 0
    duplicate_link_correct: int = 0
    audit_chains_verified: int = 0
    audit_chains_total: int = 0
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)


def _observed_route(state: InvoiceState) -> str:
    if state in (S.AUTO_APPROVED, S.EXPORTED, S.EXPORT_FAILED):
        return "auto_approve"
    if state is S.IN_REVIEW:
        return "review"
    return state.value.lower()


def build_local_pipeline(
    conn: sqlite3.Connection,
    workdir: Path,
    validator: Validator,
    purchase_orders: list[PurchaseOrder],
    truths: dict[str, Invoice],
) -> tuple[Pipeline, SqliteAuditLog]:
    pos = SqlitePurchaseOrderRepository(conn)
    pos.load(purchase_orders)
    audit = SqliteAuditLog(conn)
    services = Services(
        store=LocalObjectStore(workdir / "objects"),
        repo=SqliteInvoiceRepository(conn),
        audit=audit,
        idempotency=SqliteIdempotencyStore(conn),
        fingerprints=SqliteFingerprintStore(conn),
        purchase_orders=pos,
        validator=validator,
        erp=InMemoryErp(),
        extractor=OracleExtractor(truths),
        clock=utc_now,
    )
    return Pipeline(services), audit


def evaluate(dataset: Path, validator: Validator, workdir: Path) -> RoutingReport:
    manifest = dataset / "manifest.jsonl"
    labels = [json.loads(line) for line in manifest.read_text("utf-8").splitlines()]
    po_doc = json.loads((dataset / "purchase_orders.json").read_text("utf-8"))
    purchase_orders = [PurchaseOrder.model_validate(p) for p in po_doc["purchase_orders"]]
    truths = {
        lbl["sha256"]: Invoice.model_validate(lbl["truth"])
        for lbl in labels
        if lbl["format"] == "pdf" and lbl["truth"] is not None
    }
    conn = connect(workdir / "pipeline.sqlite")
    try:
        return _run(conn, dataset, manifest, labels, validator, workdir, purchase_orders, truths)
    finally:
        conn.close()


def _run(
    conn: sqlite3.Connection,
    dataset: Path,
    manifest: Path,
    labels: list[dict[str, Any]],
    validator: Validator,
    workdir: Path,
    purchase_orders: list[PurchaseOrder],
    truths: dict[str, Invoice],
) -> RoutingReport:
    pipeline, audit = build_local_pipeline(conn, workdir, validator, purchase_orders, truths)
    report = RoutingReport(
        generated_at=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        git_commit=_git_commit(),
        dataset_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
    )
    doc_to_invoice: dict[str, str] = {}
    categories: dict[str, Counter[str]] = {}

    for lbl in labels:
        tenant = TenantId(lbl["tenant"])
        data = (dataset / lbl["file"]).read_bytes()
        outcome = pipeline.ingest(tenant, data)
        doc_to_invoice.setdefault(lbl["doc_id"], outcome.invoice_id)
        report.documents += 1
        cat = categories.setdefault(lbl["category"], Counter())
        cat["documents"] += 1

        if outcome.duplicate_delivery:
            observed_route = "skip_duplicate_delivery"
            observed_reasons: set[str] = set()
            dup_links: list[object] = []
        else:
            rec = pipeline.process(tenant, outcome.invoice_id)
            observed_route = _observed_route(rec.state)
            reasons = rec.meta.get("reasons")
            observed_reasons = {str(r) for r in reasons} if isinstance(reasons, list) else set()
            hits = rec.meta.get("duplicate_of")
            dup_links = (
                [h["original_invoice_id"] for h in hits if isinstance(h, dict)]
                if isinstance(hits, list)
                else []
            )
            report.exported += int(rec.state is S.EXPORTED)
            report.failed += int(rec.state is S.FAILED)
            report.audit_chains_total += 1
            report.audit_chains_verified += int(verify_chain(audit.read(tenant, rec.invoice_id)))

        expected_route = lbl["expected_route"]
        expected_reasons = set(lbl["review_reasons"])
        report.expected_review += int(expected_route == "review")
        report.observed_review += int(observed_route == "review")
        route_ok = observed_route == expected_route
        reasons_ok = observed_reasons == expected_reasons
        report.route_agree += int(route_ok)
        report.reasons_agree += int(reasons_ok)
        cat["route_agree"] += int(route_ok)
        if observed_route == "auto_approve" and expected_route != "auto_approve":
            report.false_accepts.append(lbl["doc_id"])
        if observed_route == "review" and expected_route == "auto_approve":
            report.false_reviews.append(lbl["doc_id"])
        if not (route_ok and reasons_ok):
            report.disagreements.append(
                {
                    "doc_id": lbl["doc_id"],
                    "variant": lbl["variant"],
                    "observed": [observed_route, sorted(observed_reasons)],
                    "expected": [expected_route, sorted(expected_reasons)],
                }
            )

        # Duplicate detection: a business duplicate must be flagged and linked to its original.
        is_dup = lbl["category"] == "duplicate" and lbl["variant"] != "exact_resend"
        flagged = "duplicate_suspected" in observed_reasons
        if is_dup and flagged:
            report.duplicate_tp += 1
            original = doc_to_invoice.get(lbl["duplicate_of"])
            report.duplicate_link_correct += int(original in dup_links)
        elif is_dup:
            report.duplicate_fn += 1
        elif flagged:
            report.duplicate_fp += 1

    report.by_category = {k: dict(v) for k, v in sorted(categories.items())}
    return report


def _ratio(a: int, b: int) -> str:
    return f"{a} / {b}" + (f" ({a / b * 100:.2f} %)" if b else "")


def to_markdown(r: RoutingReport) -> str:
    dup_p = r.duplicate_tp + r.duplicate_fp
    dup_r = r.duplicate_tp + r.duplicate_fn
    lines = [
        f"# Routing evaluation (local pipeline, end to end), {r.generated_at[:10]}",
        "",
        f"- Commit `{r.git_commit}`, dataset manifest sha256 `{r.dataset_manifest_sha256[:16]}…`",
        "- Pipeline: local runner with the real KoSIT validator, SQLite stores, in-memory ERP.",
        "- **Plain PDFs use an oracle extractor (ground truth).** These numbers measure "
        "validation, duplicate detection, 2-way matching, routing and export, but **not** "
        "extraction, which is W4.",
        "- Data: synthetic and generated by this project. Results do not predict real-world "
        "accuracy.",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Route equal to label | {_ratio(r.route_agree, r.documents)} |",
        f"| Review reasons equal to label (exact set) | {_ratio(r.reasons_agree, r.documents)} |",
        f"| **False accepts** (auto-approved, label says otherwise) | {len(r.false_accepts)} |",
        f"| False reviews (sent to review, label says auto-approve) | {len(r.false_reviews)} |",
        f"| Review rate, observed vs. expected | {r.observed_review} vs. {r.expected_review} "
        f"of {r.documents} |",
        f"| Duplicate detection precision | {_ratio(r.duplicate_tp, dup_p)} |",
        f"| Duplicate detection recall | {_ratio(r.duplicate_tp, dup_r)} |",
        "| Duplicates linked to the right original | "
        f"{_ratio(r.duplicate_link_correct, r.duplicate_tp)} |",
        f"| Exported to ERP | {r.exported} |",
        f"| Failed | {r.failed} |",
        f"| Audit hash chains verified | {_ratio(r.audit_chains_verified, r.audit_chains_total)} |",
        "",
        "## Route agreement by category",
        "",
        "| Category | Documents | Route equal to label |",
        "|---|---|---|",
    ]
    for name, c in r.by_category.items():
        lines.append(f"| {name} | {c.get('documents', 0)} | {c.get('route_agree', 0)} |")
    if r.disagreements:
        lines += ["", "## Disagreements", ""] + [f"- {d}" for d in r.disagreements]
    lines += [
        "",
        "## Reading this report",
        "",
        "Labels and pipeline share this project's reason codes, and the PO-mismatch and "
        "duplicate cases were designed with the matching rules in mind (ADR-0014). Full "
        "agreement is therefore a **regression gate**, not proof that the rules are right "
        "for a real AP department. The tolerance values are still a proposal awaiting "
        "the owner's confirmation.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    from rechnungsfluss.validation.kosit import KositValidator

    parser = argparse.ArgumentParser(description="End-to-end routing evaluation.")
    parser.add_argument("--dataset", type=Path, default=Path("eval/dataset"))
    parser.add_argument("--out", type=Path, default=Path("eval/reports"))
    parser.add_argument("--kosit-url", default="http://127.0.0.1:8081")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        report = evaluate(args.dataset, KositValidator(args.kosit_url, timeout_s=60), Path(tmp))
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.out / f"{report.generated_at[:10]}-routing"
    stem.with_suffix(".json").write_text(
        json.dumps(asdict(report), indent=1, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    stem.with_suffix(".md").write_text(to_markdown(report), encoding="utf-8", newline="\n")
    print(f"wrote {stem}.json and {stem}.md")  # noqa: T201


if __name__ == "__main__":
    main()
