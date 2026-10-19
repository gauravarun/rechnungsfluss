"""AWS adapters against moto (in-process AWS emulation, ADR-0009).

Table definitions mirror infra/terraform/modules/storage. moto does not
evaluate IAM policies, so these tests cover adapter behaviour and the
application-level tenant guard only; IAM isolation is tested on the policy
documents themselves (test_iam_policies.py) and needs a real account to prove.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator
from decimal import Decimal

import boto3
import pytest
from boto3.session import Session
from moto import mock_aws

from rechnungsfluss.adapters.aws import (
    DynamoAuditLog,
    DynamoFingerprintStore,
    DynamoIdempotencyStore,
    DynamoInvoiceRepository,
    DynamoPurchaseOrderRepository,
    S3ObjectStore,
    TenantMismatchError,
)
from rechnungsfluss.adapters.local import InMemoryErp
from rechnungsfluss.application.pipeline import Pipeline, Services
from rechnungsfluss.domain.audit import AuditAction, AuditEvent, append_event, verify_chain
from rechnungsfluss.domain.duplicates import fingerprint
from rechnungsfluss.domain.matching import PurchaseOrder, PurchaseOrderLine
from rechnungsfluss.domain.states import InvoiceState
from rechnungsfluss.domain.tenancy import TenantId
from rechnungsfluss.formats import SPEC_EN16931, cii
from rechnungsfluss.ports import ConflictError, InvoiceRecord, NotFoundError
from tests.conftest import make_invoice
from tests.fakes import StubValidator

REGION = "eu-central-1"
BUCKET = "rf-test-docs"
MAIN, AUDIT, IDEM = "rf-test-main", "rf-test-audit", "rf-test-idempotency"
T0 = dt.datetime(2026, 10, 19, 8, 0, tzinfo=dt.UTC)


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(var, "testing")  # moto only; never real credentials
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        s = boto3.session.Session(region_name=REGION)
        s.client("s3").create_bucket(
            Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": "eu-central-1"}
        )
        ddb = s.client("dynamodb")
        ddb.create_table(
            TableName=MAIN,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": n, "AttributeType": "S"} for n in ("pk", "sk", "gsi1pk", "gsi1sk")
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "gsi1",
                    "KeySchema": [
                        {"AttributeName": "gsi1pk", "KeyType": "HASH"},
                        {"AttributeName": "gsi1sk", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        ddb.create_table(
            TableName=AUDIT,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "seq", "AttributeType": "N"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "seq", "KeyType": "RANGE"},
            ],
        )
        ddb.create_table(
            TableName=IDEM,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        )
        yield s


def _record(invoice_id: str = "inv-1") -> InvoiceRecord:
    return InvoiceRecord(
        invoice_id=invoice_id, state=InvoiceState.RECEIVED, version=0, document_sha256="ab" * 32
    )


def test_s3_round_trip_and_tenant_prefix(session: Session, tenant_de: TenantId) -> None:
    store = S3ObjectStore(session, tenant_de, BUCKET)
    store.put(tenant_de, "documents/a", b"x")
    assert store.get(tenant_de, "documents/a") == b"x"
    keys = [o["Key"] for o in session.client("s3").list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert keys == ["nordwerk-de/documents/a"]
    with pytest.raises(NotFoundError):
        store.get(tenant_de, "documents/missing")


@pytest.mark.tenancy
@pytest.mark.parametrize("key", ["../nordwerk-at/x", "/abs", "a/../../b"])
def test_s3_rejects_escaping_keys(session: Session, tenant_de: TenantId, key: str) -> None:
    with pytest.raises(ValueError, match="escapes tenant scope"):
        S3ObjectStore(session, tenant_de, BUCKET).put(tenant_de, key, b"x")


@pytest.mark.tenancy
def test_adapters_refuse_other_tenants(
    session: Session, tenant_de: TenantId, tenant_at: TenantId
) -> None:
    adapters: list[Callable[[], object]] = [
        lambda: S3ObjectStore(session, tenant_de, BUCKET).get(tenant_at, "x"),
        lambda: DynamoInvoiceRepository(session, tenant_de, MAIN).get(tenant_at, "x"),
        lambda: DynamoAuditLog(session, tenant_de, AUDIT).read(tenant_at, "x"),
        lambda: DynamoIdempotencyStore(session, tenant_de, IDEM).claim(tenant_at, "k"),
        lambda: DynamoFingerprintStore(session, tenant_de, MAIN).for_seller(tenant_at, "s"),
        lambda: DynamoPurchaseOrderRepository(session, tenant_de, MAIN).get(tenant_at, "PO-1"),
    ]
    for call in adapters:
        with pytest.raises(TenantMismatchError):
            call()


def test_invoice_repository_concurrency_and_gsi(session: Session, tenant_de: TenantId) -> None:
    repo = DynamoInvoiceRepository(session, tenant_de, MAIN)
    repo.create(tenant_de, _record())
    with pytest.raises(ConflictError):
        repo.create(tenant_de, _record())
    parsed = InvoiceRecord(
        "inv-1", InvoiceState.PARSED, 0, "ab" * 32, make_invoice(), {"format": "cii"}
    )
    repo.update(tenant_de, parsed, expected_version=0)
    with pytest.raises(ConflictError):
        repo.update(tenant_de, parsed, expected_version=0)
    with pytest.raises(NotFoundError):
        repo.update(tenant_de, _record("nope"), expected_version=0)
    got = repo.get(tenant_de, "inv-1")
    assert (got.state, got.version, got.meta, got.invoice) == (
        InvoiceState.PARSED,
        1,
        {"format": "cii"},
        make_invoice(),
    )
    assert [r.invoice_id for r in repo.list_by_state(tenant_de, InvoiceState.PARSED)] == ["inv-1"]
    assert repo.list_by_state(tenant_de, InvoiceState.RECEIVED) == []


def test_audit_log_is_append_only(session: Session, tenant_de: TenantId) -> None:
    log = DynamoAuditLog(session, tenant_de, AUDIT)
    chain: list[AuditEvent] = []
    for i in range(3):
        event = append_event(
            chain,
            tenant=tenant_de,
            invoice_id="inv-1",
            actor="system:test",
            action=AuditAction.STATE_CHANGED,
            at=T0 + dt.timedelta(seconds=i),
        )
        log.append(tenant_de, event)
        chain.append(event)
    with pytest.raises(ConflictError):
        log.append(tenant_de, chain[1])
    stored = log.read(tenant_de, "inv-1")
    assert list(stored) == chain
    assert verify_chain(stored)


def test_idempotency_claims_once(
    session: Session, tenant_de: TenantId, tenant_at: TenantId
) -> None:
    de = DynamoIdempotencyStore(session, tenant_de, IDEM)
    at = DynamoIdempotencyStore(session, tenant_at, IDEM)
    assert de.claim(tenant_de, "ingest:abc") is True
    assert de.claim(tenant_de, "ingest:abc") is False
    assert at.claim(tenant_at, "ingest:abc") is True  # same key, other tenant: independent


def test_fingerprints_and_purchase_orders(session: Session, tenant_de: TenantId) -> None:
    fps = DynamoFingerprintStore(session, tenant_de, MAIN)
    fp = fingerprint("inv-1", make_invoice())
    fps.add(tenant_de, fp)
    assert list(fps.for_seller(tenant_de, fp.seller_key)) == [fp]
    pos = DynamoPurchaseOrderRepository(session, tenant_de, MAIN)
    po = PurchaseOrder(
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
    pos.put(tenant_de, po)
    assert pos.get(tenant_de, "PO-DE-1001") == po
    assert pos.get(tenant_de, "PO-DE-9999") is None
    assert pos.get(tenant_de, "bad#key") is None
    with pytest.raises(TenantMismatchError):
        pos.put(tenant_de, po.model_copy(update={"tenant": "nordwerk-at"}))


def test_full_pipeline_on_aws_adapters(session: Session, tenant_de: TenantId) -> None:
    """Same Pipeline code as local mode, only the adapters differ (ADR-0009)."""
    pos = DynamoPurchaseOrderRepository(session, tenant_de, MAIN)
    pos.put(
        tenant_de,
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
        ),
    )
    clock = iter(T0 + dt.timedelta(seconds=i) for i in range(1000))
    pipeline = Pipeline(
        Services(
            store=S3ObjectStore(session, tenant_de, BUCKET),
            repo=DynamoInvoiceRepository(session, tenant_de, MAIN),
            audit=DynamoAuditLog(session, tenant_de, AUDIT),
            idempotency=DynamoIdempotencyStore(session, tenant_de, IDEM),
            fingerprints=DynamoFingerprintStore(session, tenant_de, MAIN),
            purchase_orders=pos,
            validator=StubValidator(),
            erp=InMemoryErp(),
            extractor=None,
            clock=lambda: next(clock),
        )
    )
    xml = cii.to_cii(make_invoice(), specification_id=SPEC_EN16931)
    outcome = pipeline.ingest(tenant_de, xml)
    rec = pipeline.process(tenant_de, outcome.invoice_id)
    assert rec.state is InvoiceState.EXPORTED
    assert pipeline.ingest(tenant_de, xml).duplicate_delivery
    assert verify_chain(pipeline.s.audit.read(tenant_de, rec.invoice_id))
