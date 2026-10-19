"""Lambda handlers with a fake runtime built on local adapters."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Iterator, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from rechnungsfluss.adapters.local import (
    InMemoryErp,
    LocalObjectStore,
    SqliteAuditLog,
    SqliteFingerprintStore,
    SqliteIdempotencyStore,
    SqliteInvoiceRepository,
    SqlitePurchaseOrderRepository,
    connect,
)
from rechnungsfluss.application.pipeline import Pipeline, Services
from rechnungsfluss.domain.matching import PurchaseOrder, PurchaseOrderLine
from rechnungsfluss.domain.states import InvoiceState
from rechnungsfluss.domain.tenancy import TenantId
from rechnungsfluss.formats import SPEC_EN16931, cii
from rechnungsfluss.handlers.lambdas import ExportFailedError, Handlers, UnknownTenantError
from tests.conftest import make_invoice
from tests.fakes import StubValidator

BUCKET = "rf-dev-docs"


class FakeRuntime:
    def __init__(self, conn: sqlite3.Connection, root: Path) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.executions: dict[str, Mapping[str, str]] = {}
        self.erp = InMemoryErp()
        pos = SqlitePurchaseOrderRepository(conn)
        pos.load(
            [
                PurchaseOrder(
                    tenant="nordwerk-de",
                    number="PO-DE-1001",
                    supplier_vat_id="DE999999999",
                    supplier_name="Muster Schrauben KG (synthetic)",
                    currency="EUR",
                    lines=(
                        PurchaseOrderLine(
                            line_no=1,
                            description="Sechskantschraube M8",
                            quantity=Decimal("10"),
                            unit_code="H87",
                            net_price=Decimal("12.50"),
                        ),
                    ),
                )
            ]
        )
        self._pipeline = Pipeline(
            Services(
                store=LocalObjectStore(root),
                repo=SqliteInvoiceRepository(conn),
                audit=SqliteAuditLog(conn),
                idempotency=SqliteIdempotencyStore(conn),
                fingerprints=SqliteFingerprintStore(conn),
                purchase_orders=pos,
                validator=StubValidator(),
                erp=self.erp,
                extractor=None,
                clock=lambda: dt.datetime.now(dt.UTC),
            ),
            inline_export=False,
        )

    def known_tenants(self) -> frozenset[str]:
        return frozenset({"nordwerk-de", "nordwerk-at"})

    def pipeline(self, tenant: TenantId) -> Pipeline:
        return self._pipeline

    def read_inbox_object(self, tenant: TenantId, bucket: str, key: str) -> bytes:
        return self.objects[(bucket, key)]

    def start_execution(self, name: str, payload: Mapping[str, str]) -> bool:
        if name in self.executions:
            return False
        self.executions[name] = payload
        return True


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[FakeRuntime]:
    conn = connect(":memory:")
    yield FakeRuntime(conn, tmp_path)
    conn.close()


def _sqs_event(*keys: str, size: int = 1000) -> dict[str, Any]:
    return {
        "Records": [
            {
                "messageId": f"m{i}",
                "body": json.dumps(
                    {
                        "source": "aws.s3",
                        "detail-type": "Object Created",
                        "detail": {
                            "bucket": {"name": BUCKET},
                            "object": {"key": key, "size": size},
                        },
                    }
                ),
            }
            for i, key in enumerate(keys)
        ]
    }


def _upload(runtime: FakeRuntime, key: str) -> None:
    runtime.objects[(BUCKET, key)] = cii.to_cii(make_invoice(), specification_id=SPEC_EN16931)


def test_ingest_starts_one_execution_per_document(runtime: FakeRuntime) -> None:
    key = "nordwerk-de/inbox/a.xml"
    _upload(runtime, key)
    handlers = Handlers(runtime)
    assert handlers.ingest(_sqs_event(key)) == {"batchItemFailures": []}
    # SQS redelivers the same event: no second invoice, no second execution.
    assert handlers.ingest(_sqs_event(key)) == {"batchItemFailures": []}
    assert len(runtime.executions) == 1
    ((name, payload),) = runtime.executions.items()
    assert name == f"nordwerk-de-{payload['invoice_id']}"
    assert payload["tenant"] == "nordwerk-de"


@pytest.mark.tenancy
@pytest.mark.parametrize(
    "key",
    [
        "unknown-co/inbox/a.xml",  # tenant not configured
        "nordwerk-de/outbox/a.xml",  # not an inbox key
        "nordwerk-de/inbox/../x.xml",  # path tricks
        "NORDWERK-DE/inbox/a.xml",  # not a valid tenant id
    ],
)
def test_bad_keys_fail_the_record_so_it_reaches_the_dlq(runtime: FakeRuntime, key: str) -> None:
    runtime.objects[(BUCKET, key)] = b"x"
    result = Handlers(runtime).ingest(_sqs_event(key))
    assert result == {"batchItemFailures": [{"itemIdentifier": "m0"}]}
    assert runtime.executions == {}


def test_oversized_documents_are_refused(runtime: FakeRuntime) -> None:
    key = "nordwerk-de/inbox/big.xml"
    _upload(runtime, key)
    result = Handlers(runtime).ingest(_sqs_event(key, size=25 * 1024 * 1024))
    assert result["batchItemFailures"] == [{"itemIdentifier": "m0"}]


def test_partial_batch_failure_reports_only_the_bad_record(runtime: FakeRuntime) -> None:
    good = "nordwerk-de/inbox/good.xml"
    _upload(runtime, good)
    result = Handlers(runtime).ingest(_sqs_event(good, "nordwerk-de/nope.xml"))
    assert result == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
    assert len(runtime.executions) == 1


def _ingested(runtime: FakeRuntime) -> str:
    key = "nordwerk-de/inbox/a.xml"
    _upload(runtime, key)
    Handlers(runtime).ingest(_sqs_event(key))
    (payload,) = runtime.executions.values()
    return payload["invoice_id"]


def test_step_handler_drives_the_pipeline_one_step_at_a_time(runtime: FakeRuntime) -> None:
    invoice_id, handlers = _ingested(runtime), Handlers(runtime)
    states = [
        handlers.step({"tenant": "nordwerk-de", "invoice_id": invoice_id, "step": s})["state"]
        for s in ("parse", "validate", "check", "decide")
    ]
    assert states == ["PARSED", "VALIDATED", "MATCHED", "AUTO_APPROVED"]
    # A retried task is a no-op, not a second transition.
    again = handlers.step({"tenant": "nordwerk-de", "invoice_id": invoice_id, "step": "parse"})
    assert again["state"] == "AUTO_APPROVED"
    exported = handlers.step({"tenant": "nordwerk-de", "invoice_id": invoice_id, "step": "export"})
    assert exported["state"] == "EXPORTED"


def test_export_failure_raises_so_step_functions_retries(runtime: FakeRuntime) -> None:
    invoice_id, handlers = _ingested(runtime), Handlers(runtime)
    for s in ("parse", "validate", "check", "decide"):
        handlers.step({"tenant": "nordwerk-de", "invoice_id": invoice_id, "step": s})
    runtime.erp.fail_next = 1
    with pytest.raises(ExportFailedError):
        handlers.step({"tenant": "nordwerk-de", "invoice_id": invoice_id, "step": "export"})
    retry = handlers.step({"tenant": "nordwerk-de", "invoice_id": invoice_id, "step": "export"})
    assert retry["state"] == "EXPORTED"
    assert len(runtime.erp.booked) == 1


def test_await_review_stores_the_task_token(runtime: FakeRuntime) -> None:
    key = "nordwerk-de/inbox/bad.xml"
    t = make_invoice().totals
    bad = make_invoice(
        totals=t.model_copy(
            update={"total_with_vat": Decimal("999.99"), "amount_due": Decimal("999.99")}
        )
    )
    runtime.objects[(BUCKET, key)] = cii.to_cii(bad, specification_id=SPEC_EN16931)
    handlers = Handlers(runtime)
    handlers.ingest(_sqs_event(key))
    (payload,) = runtime.executions.values()
    invoice_id = payload["invoice_id"]
    for s in ("parse", "validate", "check", "decide"):
        handlers.step({"tenant": "nordwerk-de", "invoice_id": invoice_id, "step": s})
    token = "sfn-task-token-stand-in"  # noqa: S105 - stand-in for a Step Functions task token
    handlers.await_review({"tenant": "nordwerk-de", "invoice_id": invoice_id, "task_token": token})
    rec = runtime.pipeline(TenantId("nordwerk-de")).s.repo.get(TenantId("nordwerk-de"), invoice_id)
    assert rec.state is InvoiceState.IN_REVIEW
    assert rec.meta["review_task_token"] == token


@pytest.mark.tenancy
def test_step_handler_refuses_unknown_tenants(runtime: FakeRuntime) -> None:
    with pytest.raises(UnknownTenantError):
        Handlers(runtime).step({"tenant": "evil-corp", "invoice_id": "x", "step": "parse"})


def test_approve_without_inline_export_stops_at_approved(runtime: FakeRuntime) -> None:
    key = "nordwerk-de/inbox/bad.xml"
    inv = make_invoice(due_date=None)  # our rule sends it to review; validation passes
    runtime.objects[(BUCKET, key)] = cii.to_cii(inv, specification_id=SPEC_EN16931)
    handlers = Handlers(runtime)
    handlers.ingest(_sqs_event(key))
    (payload,) = runtime.executions.values()
    invoice_id = payload["invoice_id"]
    for s in ("parse", "validate", "check", "decide"):
        handlers.step({"tenant": "nordwerk-de", "invoice_id": invoice_id, "step": s})
    tenant = TenantId("nordwerk-de")
    rec = runtime.pipeline(tenant).approve(tenant, invoice_id, actor="user:clerk")
    assert rec.state is InvoiceState.APPROVED
    assert runtime.erp.booked == {}
