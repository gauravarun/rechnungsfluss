import datetime as dt
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from rechnungsfluss.adapters.local import (
    LocalObjectStore,
    SqliteAuditLog,
    SqliteInvoiceRepository,
    connect,
)
from rechnungsfluss.domain.audit import AuditAction, AuditEvent, append_event, verify_chain
from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.domain.states import InvoiceState
from rechnungsfluss.domain.tenancy import TenantId
from rechnungsfluss.ports import ConflictError, InvoiceRecord, NotFoundError

T0 = dt.datetime(2026, 10, 18, 9, 0, tzinfo=dt.UTC)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def repo(conn: sqlite3.Connection) -> SqliteInvoiceRepository:
    return SqliteInvoiceRepository(conn)


@pytest.fixture
def audit(conn: sqlite3.Connection) -> SqliteAuditLog:
    return SqliteAuditLog(conn)


def _record(invoice_id: str = "inv-1", invoice: Invoice | None = None) -> InvoiceRecord:
    return InvoiceRecord(
        invoice_id=invoice_id,
        state=InvoiceState.RECEIVED,
        version=0,
        document_sha256="ab" * 32,
        invoice=invoice,
    )


# --- object store ---------------------------------------------------------


def test_object_store_round_trip(tmp_path: Path, tenant_de: TenantId) -> None:
    store = LocalObjectStore(tmp_path)
    store.put(tenant_de, "inbox/a.xml", b"<x/>")
    assert store.get(tenant_de, "inbox/a.xml") == b"<x/>"


@pytest.mark.tenancy
def test_object_store_hides_other_tenants_objects(
    tmp_path: Path, tenant_de: TenantId, tenant_at: TenantId
) -> None:
    store = LocalObjectStore(tmp_path)
    store.put(tenant_de, "inbox/a.xml", b"secret")
    with pytest.raises(NotFoundError):
        store.get(tenant_at, "inbox/a.xml")


@pytest.mark.tenancy
@pytest.mark.parametrize("key", ["../nordwerk-de/inbox/a.xml", "/etc/passwd", "", "."])
def test_object_store_blocks_path_escape(tmp_path: Path, tenant_at: TenantId, key: str) -> None:
    store = LocalObjectStore(tmp_path)
    with pytest.raises(ValueError, match="escapes tenant scope"):
        store.get(tenant_at, key)


# --- invoice repository ---------------------------------------------------


def test_repo_create_get_with_invoice(
    repo: SqliteInvoiceRepository, tenant_de: TenantId, invoice: Invoice
) -> None:
    repo.create(tenant_de, _record(invoice=invoice))
    got = repo.get(tenant_de, "inv-1")
    assert got.invoice == invoice
    assert got.version == 0


def test_repo_create_twice_conflicts(repo: SqliteInvoiceRepository, tenant_de: TenantId) -> None:
    repo.create(tenant_de, _record())
    with pytest.raises(ConflictError):
        repo.create(tenant_de, _record())


def test_repo_optimistic_concurrency(repo: SqliteInvoiceRepository, tenant_de: TenantId) -> None:
    repo.create(tenant_de, _record())
    parsed = InvoiceRecord("inv-1", InvoiceState.PARSED, 0, "ab" * 32)
    repo.update(tenant_de, parsed, expected_version=0)
    assert repo.get(tenant_de, "inv-1").version == 1
    with pytest.raises(ConflictError):
        repo.update(tenant_de, parsed, expected_version=0)  # stale writer


def test_repo_update_missing_is_not_found(
    repo: SqliteInvoiceRepository, tenant_de: TenantId
) -> None:
    with pytest.raises(NotFoundError):
        repo.update(tenant_de, _record("nope"), expected_version=0)


@pytest.mark.tenancy
def test_repo_tenant_isolation(
    repo: SqliteInvoiceRepository, tenant_de: TenantId, tenant_at: TenantId
) -> None:
    repo.create(tenant_de, _record("inv-1"))
    with pytest.raises(NotFoundError):
        repo.get(tenant_at, "inv-1")
    with pytest.raises(NotFoundError):
        repo.update(tenant_at, _record("inv-1"), expected_version=0)
    assert repo.list_by_state(tenant_at, InvoiceState.RECEIVED) == []
    # Same invoice id in another tenant is a different record, not a conflict.
    repo.create(tenant_at, _record("inv-1"))


def test_repo_list_by_state(repo: SqliteInvoiceRepository, tenant_de: TenantId) -> None:
    repo.create(tenant_de, _record("inv-1"))
    repo.create(tenant_de, _record("inv-2"))
    repo.update(tenant_de, InvoiceRecord("inv-2", InvoiceState.PARSED, 0, "ab" * 32), 0)
    assert [r.invoice_id for r in repo.list_by_state(tenant_de, InvoiceState.RECEIVED)] == ["inv-1"]


# --- audit log --------------------------------------------------------------


def _event(tenant: TenantId, chain: list[AuditEvent]) -> AuditEvent:
    return append_event(
        chain,
        tenant=tenant,
        invoice_id="inv-1",
        actor="system:test",
        action=AuditAction.STATE_CHANGED,
        at=T0 + dt.timedelta(seconds=len(chain)),
        old_value="RECEIVED",
        new_value="PARSED",
    )


def test_audit_append_and_read_verifies(audit: SqliteAuditLog, tenant_de: TenantId) -> None:
    chain: list[AuditEvent] = []
    for _ in range(3):
        event = _event(tenant_de, chain)
        audit.append(tenant_de, event)
        chain.append(event)
    stored = audit.read(tenant_de, "inv-1")
    assert list(stored) == chain
    assert verify_chain(stored)


def test_audit_rejects_duplicate_seq(audit: SqliteAuditLog, tenant_de: TenantId) -> None:
    event = _event(tenant_de, [])
    audit.append(tenant_de, event)
    with pytest.raises(ConflictError):
        audit.append(tenant_de, event)


def test_audit_table_is_append_only_at_storage_level(
    conn: sqlite3.Connection, audit: SqliteAuditLog, tenant_de: TenantId
) -> None:
    audit.append(tenant_de, _event(tenant_de, []))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE audit SET event_json = '{}'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM audit")


@pytest.mark.tenancy
def test_audit_tenant_isolation(
    audit: SqliteAuditLog, tenant_de: TenantId, tenant_at: TenantId
) -> None:
    audit.append(tenant_de, _event(tenant_de, []))
    assert audit.read(tenant_at, "inv-1") == []
    with pytest.raises(ValueError, match="does not match"):
        audit.append(tenant_at, _event(tenant_de, []))
