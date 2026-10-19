"""Pipeline behaviour with fast local adapters and a stub validator.

The stub validator rejects any document whose BT-112 is 999.99 (rule ID
"STUB-1"), so validation failures can be produced without the KoSIT daemon.
The real validator is exercised in tests/integration.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

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
from rechnungsfluss.application.pipeline import Pipeline, ReviewActionError, Services
from rechnungsfluss.domain.audit import AuditAction, verify_chain
from rechnungsfluss.domain.invoice import Invoice, Totals
from rechnungsfluss.domain.matching import PurchaseOrder, PurchaseOrderLine
from rechnungsfluss.domain.states import InvoiceState
from rechnungsfluss.domain.tenancy import TenantId
from rechnungsfluss.formats import SPEC_EN16931, cii, zugferd
from rechnungsfluss.formats.cii import to_cii_minimum
from rechnungsfluss.ports import NotFoundError
from rechnungsfluss.synthetic.render import render_pdf
from tests.conftest import make_invoice
from tests.fakes import FixedExtractor, StubValidator

S = InvoiceState
T0 = dt.datetime(2026, 10, 19, 8, 0, tzinfo=dt.UTC)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def erp() -> InMemoryErp:
    return InMemoryErp()


@pytest.fixture
def validator() -> StubValidator:
    return StubValidator()


def _po(tenant: str = "nordwerk-de") -> PurchaseOrder:
    return PurchaseOrder(
        tenant=tenant,
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


def _pipeline(
    conn: sqlite3.Connection,
    tmp_path: Path,
    validator: StubValidator,
    erp: InMemoryErp,
    extractor: FixedExtractor | None = None,
) -> Pipeline:
    pos = SqlitePurchaseOrderRepository(conn)
    pos.load([_po("nordwerk-de"), _po("nordwerk-at")])
    clock = iter(T0 + dt.timedelta(seconds=i) for i in range(10_000))
    return Pipeline(
        Services(
            store=LocalObjectStore(tmp_path),
            repo=SqliteInvoiceRepository(conn),
            audit=SqliteAuditLog(conn),
            idempotency=SqliteIdempotencyStore(conn),
            fingerprints=SqliteFingerprintStore(conn),
            purchase_orders=pos,
            validator=validator,
            erp=erp,
            extractor=extractor,
            clock=lambda: next(clock),
        )
    )


@pytest.fixture
def pipeline(
    conn: sqlite3.Connection, tmp_path: Path, validator: StubValidator, erp: InMemoryErp
) -> Pipeline:
    return _pipeline(conn, tmp_path, validator, erp)


def _xml(inv: Invoice | None = None) -> bytes:
    return cii.to_cii(inv or make_invoice(), specification_id=SPEC_EN16931)


def _states(pipeline: Pipeline, tenant: TenantId, invoice_id: str) -> list[str]:
    events = pipeline.s.audit.read(tenant, invoice_id)
    return [str(e.new_value) for e in events if e.action is AuditAction.STATE_CHANGED]


def _bad_total() -> Invoice:
    t = make_invoice().totals
    return make_invoice(
        totals=Totals.model_validate(
            {**t.model_dump(), "total_with_vat": Decimal("999.99"), "amount_due": Decimal("999.99")}
        )
    )


# --- happy path and idempotency ---------------------------------------------------


def test_clean_invoice_is_exported_and_every_step_audited(
    pipeline: Pipeline, tenant_de: TenantId, erp: InMemoryErp
) -> None:
    outcome = pipeline.ingest(tenant_de, _xml())
    rec = pipeline.process(tenant_de, outcome.invoice_id)
    assert rec.state is S.EXPORTED
    assert rec.meta["erp_document_id"]
    assert _states(pipeline, tenant_de, rec.invoice_id) == [
        "RECEIVED",
        "PARSED",
        "VALIDATED",
        "MATCHED",
        "AUTO_APPROVED",
        "EXPORTED",
    ]
    assert verify_chain(pipeline.s.audit.read(tenant_de, rec.invoice_id))
    assert len(erp.booked) == 1


def test_same_bytes_twice_create_one_invoice(pipeline: Pipeline, tenant_de: TenantId) -> None:
    first = pipeline.ingest(tenant_de, _xml())
    second = pipeline.ingest(tenant_de, _xml())
    assert not first.duplicate_delivery
    assert second.duplicate_delivery
    assert second.invoice_id == first.invoice_id


def test_process_twice_is_a_no_op(
    pipeline: Pipeline, tenant_de: TenantId, validator: StubValidator, erp: InMemoryErp
) -> None:
    invoice_id = pipeline.ingest(tenant_de, _xml()).invoice_id
    pipeline.process(tenant_de, invoice_id)
    calls, events = validator.calls, len(pipeline.s.audit.read(tenant_de, invoice_id))
    assert pipeline.process(tenant_de, invoice_id).state is S.EXPORTED
    assert validator.calls == calls
    assert len(pipeline.s.audit.read(tenant_de, invoice_id)) == events
    assert len(erp.booked) == 1


# --- routing to review -------------------------------------------------------------


def test_validation_failure_goes_to_review(pipeline: Pipeline, tenant_de: TenantId) -> None:
    rec = pipeline.process(tenant_de, pipeline.ingest(tenant_de, _xml(_bad_total())).invoice_id)
    assert rec.state is S.IN_REVIEW
    assert rec.meta["reasons"] == ["validation_failed"]
    assert rec.meta["validation"] == {"accepted": False, "scenario": "stub", "rule_ids": ["STUB-1"]}


def test_business_duplicate_goes_to_review(pipeline: Pipeline, tenant_de: TenantId) -> None:
    first = pipeline.process(tenant_de, pipeline.ingest(tenant_de, _xml()).invoice_id)
    ubl_copy = make_invoice()  # same invoice, different bytes (another delivery channel)
    from rechnungsfluss.formats import ubl

    second_id = pipeline.ingest(tenant_de, ubl.to_ubl(ubl_copy, specification_id=SPEC_EN16931))
    rec = pipeline.process(tenant_de, second_id.invoice_id)
    assert rec.state is S.IN_REVIEW
    assert rec.meta["reasons"] == ["duplicate_suspected"]
    assert rec.meta["duplicate_of"] == [{"original_invoice_id": first.invoice_id, "kind": "exact"}]


def test_minimum_profile_can_only_be_rejected(pipeline: Pipeline, tenant_de: TenantId) -> None:
    import facturx

    inv = make_invoice()
    pdf = facturx.generate_from_binary(
        render_pdf(inv), to_cii_minimum(inv), flavor="factur-x", level="minimum", check_xsd=True
    )
    rec = pipeline.process(tenant_de, pipeline.ingest(tenant_de, pdf).invoice_id)
    assert rec.state is S.IN_REVIEW
    assert rec.meta["reasons"] == ["not_an_e_invoice"]
    with pytest.raises(ReviewActionError, match="request an e-invoice"):
        pipeline.approve(tenant_de, rec.invoice_id, actor="user:clerk")
    rejected = pipeline.reject(
        tenant_de, rec.invoice_id, actor="user:clerk", reason="please send XRechnung"
    )
    assert rejected.state is S.REJECTED


def test_zugferd_en16931_is_processed(pipeline: Pipeline, tenant_de: TenantId) -> None:
    inv = make_invoice()
    pdf = zugferd.embed(render_pdf(inv), inv)
    rec = pipeline.process(tenant_de, pipeline.ingest(tenant_de, pdf).invoice_id)
    assert rec.state is S.EXPORTED
    assert rec.meta["format"] == "zugferd"


# --- plain PDFs ----------------------------------------------------------------------


def test_plain_pdf_without_extractor_fails_then_replays(
    conn: sqlite3.Connection,
    tmp_path: Path,
    validator: StubValidator,
    erp: InMemoryErp,
    tenant_de: TenantId,
) -> None:
    pipeline = _pipeline(conn, tmp_path, validator, erp, extractor=None)
    invoice_id = pipeline.ingest(tenant_de, render_pdf(make_invoice())).invoice_id
    rec = pipeline.process(tenant_de, invoice_id)
    assert rec.state is S.FAILED
    assert "no extractor" in str(rec.meta["error"])
    # An operator configures extraction and replays the failed item.
    fixed = _pipeline(conn, tmp_path, validator, erp, extractor=FixedExtractor(make_invoice()))
    assert fixed.replay(tenant_de, invoice_id, actor="user:operator").state is S.EXPORTED
    actions = [e.action for e in fixed.s.audit.read(tenant_de, invoice_id)]
    assert AuditAction.REPLAYED in actions


def test_garbage_document_fails(pipeline: Pipeline, tenant_de: TenantId) -> None:
    rec = pipeline.process(tenant_de, pipeline.ingest(tenant_de, b"<html/>").invoice_id)
    assert rec.state is S.FAILED
    assert "unsupported document" in str(rec.meta["error"])


# --- review actions ------------------------------------------------------------------


def test_invalid_document_cannot_be_approved_without_corrections(
    pipeline: Pipeline, tenant_de: TenantId
) -> None:
    rec = pipeline.process(tenant_de, pipeline.ingest(tenant_de, _xml(_bad_total())).invoice_id)
    with pytest.raises(ReviewActionError, match="failed official validation"):
        pipeline.approve(tenant_de, rec.invoice_id, actor="user:clerk")


def test_correction_is_revalidated_audited_and_exported(
    pipeline: Pipeline, tenant_de: TenantId, erp: InMemoryErp
) -> None:
    rec = pipeline.process(tenant_de, pipeline.ingest(tenant_de, _xml(_bad_total())).invoice_id)
    with pytest.raises(ReviewActionError, match="still fails validation"):
        pipeline.approve(
            tenant_de,
            rec.invoice_id,
            actor="user:clerk",
            corrections={"totals.total_with_vat": "999.99"},
        )
    done = pipeline.approve(
        tenant_de,
        rec.invoice_id,
        actor="user:clerk",
        corrections={"totals.total_with_vat": "148.75", "totals.amount_due": "148.75"},
    )
    assert done.state is S.EXPORTED
    assert done.invoice is not None
    assert done.invoice.totals.total_with_vat == Decimal("148.75")
    changes = [
        e
        for e in pipeline.s.audit.read(tenant_de, rec.invoice_id)
        if e.action is AuditAction.FIELD_CHANGED
    ]
    assert {(e.field, e.old_value, e.new_value, e.actor) for e in changes} == {
        ("totals.total_with_vat", "999.99", "148.75", "user:clerk"),
        ("totals.amount_due", "999.99", "148.75", "user:clerk"),
    }
    assert verify_chain(pipeline.s.audit.read(tenant_de, rec.invoice_id))
    assert list(erp.booked) == [f"{tenant_de.value}:{rec.invoice_id}:v1"]


@pytest.mark.parametrize("path", ["seller.name", "buyer.vat_id", "specification_id", "provenance"])
def test_protected_fields_cannot_be_corrected(
    pipeline: Pipeline, tenant_de: TenantId, path: str
) -> None:
    rec = pipeline.process(tenant_de, pipeline.ingest(tenant_de, _xml(_bad_total())).invoice_id)
    with pytest.raises(ReviewActionError, match="cannot be corrected"):
        pipeline.approve(tenant_de, rec.invoice_id, actor="user:clerk", corrections={path: "x"})


def test_unknown_correction_path(pipeline: Pipeline, tenant_de: TenantId) -> None:
    rec = pipeline.process(tenant_de, pipeline.ingest(tenant_de, _xml(_bad_total())).invoice_id)
    with pytest.raises(ReviewActionError, match="unknown field"):
        pipeline.approve(
            tenant_de, rec.invoice_id, actor="user:clerk", corrections={"lines[5].quantity": "1"}
        )


def test_reject_needs_a_reason_and_review_state(pipeline: Pipeline, tenant_de: TenantId) -> None:
    exported = pipeline.process(tenant_de, pipeline.ingest(tenant_de, _xml()).invoice_id)
    with pytest.raises(ReviewActionError, match="expected IN_REVIEW"):
        pipeline.reject(tenant_de, exported.invoice_id, actor="user:clerk", reason="no")
    with pytest.raises(ReviewActionError, match="needs a reason"):
        pipeline.reject(tenant_de, exported.invoice_id, actor="user:clerk", reason="  ")


# --- export failures -----------------------------------------------------------------


def test_erp_outage_then_replay_books_exactly_once(
    pipeline: Pipeline, tenant_de: TenantId, erp: InMemoryErp
) -> None:
    erp.fail_next = 1
    rec = pipeline.process(tenant_de, pipeline.ingest(tenant_de, _xml()).invoice_id)
    assert rec.state is S.EXPORT_FAILED
    assert pipeline.replay(tenant_de, rec.invoice_id, actor="user:operator").state is S.EXPORTED
    assert len(erp.booked) == 1


def test_erp_returns_original_result_for_a_replayed_key(
    erp: InMemoryErp, tenant_de: TenantId
) -> None:
    first = erp.post_invoice(tenant_de, "i1", make_invoice(), "k")
    again = erp.post_invoice(tenant_de, "i1", make_invoice(), "k")
    assert (first.replayed, again.replayed) == (False, True)
    assert first.erp_document_id == again.erp_document_id


def test_replay_refuses_healthy_invoices(pipeline: Pipeline, tenant_de: TenantId) -> None:
    rec = pipeline.process(tenant_de, pipeline.ingest(tenant_de, _xml()).invoice_id)
    with pytest.raises(ReviewActionError, match="cannot be replayed"):
        pipeline.replay(tenant_de, rec.invoice_id, actor="user:operator")


# --- tenancy ---------------------------------------------------------------------------


@pytest.mark.tenancy
def test_other_tenant_cannot_see_process_or_review(
    pipeline: Pipeline, tenant_de: TenantId, tenant_at: TenantId
) -> None:
    rec = pipeline.process(tenant_de, pipeline.ingest(tenant_de, _xml(_bad_total())).invoice_id)
    with pytest.raises(NotFoundError):
        pipeline.process(tenant_at, rec.invoice_id)
    with pytest.raises(NotFoundError):
        pipeline.reject(tenant_at, rec.invoice_id, actor="user:clerk-at", reason="x")
    assert pipeline.s.audit.read(tenant_at, rec.invoice_id) == []


@pytest.mark.tenancy
def test_same_document_in_two_tenants_is_two_invoices(
    pipeline: Pipeline, tenant_de: TenantId, tenant_at: TenantId
) -> None:
    de = pipeline.ingest(tenant_de, _xml())
    at = pipeline.ingest(tenant_at, _xml())
    assert not at.duplicate_delivery
    pipeline.process(tenant_de, de.invoice_id)
    rec_at = pipeline.process(tenant_at, at.invoice_id)
    assert "duplicate_suspected" not in rec_at.meta.get("reasons", [])  # type: ignore[operator]
