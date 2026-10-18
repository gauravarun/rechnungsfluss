"""Local-mode adapters: filesystem object store and SQLite repositories.

Used by ``RF_MODE=local`` and by tests. These enforce tenant isolation in
application code only; the IAM and KMS layers of ADR-0006 exist only in AWS.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from rechnungsfluss.domain.audit import AuditEvent
from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.domain.states import InvoiceState
from rechnungsfluss.domain.tenancy import TenantId, entity_key, tenant_prefix
from rechnungsfluss.ports import ConflictError, InvoiceRecord, NotFoundError


class LocalObjectStore:
    """Stores objects under ``<root>/<tenant>/<key>``."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _path(self, tenant: TenantId, key: str) -> Path:
        base = (self._root / tenant.value).resolve()
        path = (base / key).resolve()
        # Blocks "../other-tenant/..." and absolute keys.
        if not path.is_relative_to(base) or path == base:
            raise ValueError(f"object key escapes tenant scope: {key!r}")
        return path

    def put(self, tenant: TenantId, key: str, data: bytes) -> None:
        path = self._path(tenant, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, tenant: TenantId, key: str) -> bytes:
        path = self._path(tenant, key)
        if not path.is_file():
            raise NotFoundError(key)
        return path.read_bytes()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    pk TEXT PRIMARY KEY,
    tenant TEXT NOT NULL,
    state TEXT NOT NULL,
    version INTEGER NOT NULL,
    document_sha256 TEXT NOT NULL,
    invoice_json TEXT
);
CREATE INDEX IF NOT EXISTS invoices_by_state ON invoices (tenant, state);

CREATE TABLE IF NOT EXISTS audit (
    pk TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_json TEXT NOT NULL,
    PRIMARY KEY (pk, seq)
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.executescript(_SCHEMA)
    return conn


def _row_to_record(row: tuple[str, str, int, str, str | None]) -> InvoiceRecord:
    invoice_id, state, version, sha, invoice_json = row
    return InvoiceRecord(
        invoice_id=invoice_id,
        state=InvoiceState(state),
        version=version,
        document_sha256=sha,
        invoice=Invoice.model_validate_json(invoice_json) if invoice_json else None,
    )


class SqliteInvoiceRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @staticmethod
    def _pk(tenant: TenantId, invoice_id: str) -> str:
        return entity_key(tenant, "INV", invoice_id)

    @staticmethod
    def _json(record: InvoiceRecord) -> str | None:
        return record.invoice.model_dump_json() if record.invoice else None

    def create(self, tenant: TenantId, record: InvoiceRecord) -> None:
        try:
            self._conn.execute(
                "INSERT INTO invoices VALUES (?, ?, ?, 0, ?, ?)",
                (
                    self._pk(tenant, record.invoice_id),
                    tenant.value,
                    record.state.value,
                    record.document_sha256,
                    self._json(record),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"invoice {record.invoice_id} already exists") from exc

    def update(self, tenant: TenantId, record: InvoiceRecord, expected_version: int) -> None:
        cur = self._conn.execute(
            "UPDATE invoices SET state = ?, version = version + 1, invoice_json = ? "
            "WHERE pk = ? AND version = ?",
            (
                record.state.value,
                self._json(record),
                self._pk(tenant, record.invoice_id),
                expected_version,
            ),
        )
        if cur.rowcount != 1:
            self.get(tenant, record.invoice_id)  # raises NotFoundError if absent
            raise ConflictError(f"version conflict on invoice {record.invoice_id}")

    def get(self, tenant: TenantId, invoice_id: str) -> InvoiceRecord:
        row = self._conn.execute(
            "SELECT ?, state, version, document_sha256, invoice_json FROM invoices WHERE pk = ?",
            (invoice_id, self._pk(tenant, invoice_id)),
        ).fetchone()
        if row is None:
            raise NotFoundError(invoice_id)
        return _row_to_record(row)

    def list_by_state(self, tenant: TenantId, state: InvoiceState) -> Sequence[InvoiceRecord]:
        prefix = tenant_prefix(tenant) + "INV#"
        rows = self._conn.execute(
            "SELECT substr(pk, ?), state, version, document_sha256, invoice_json "
            "FROM invoices WHERE tenant = ? AND state = ? AND substr(pk, 1, ?) = ? ORDER BY pk",
            (len(prefix) + 1, tenant.value, state.value, len(prefix), prefix),
        ).fetchall()
        return [_row_to_record(r) for r in rows]


class SqliteAuditLog:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def append(self, tenant: TenantId, event: AuditEvent) -> None:
        if event.tenant != tenant.value:
            raise ValueError("event tenant does not match caller tenant")
        try:
            self._conn.execute(
                "INSERT INTO audit VALUES (?, ?, ?)",
                (entity_key(tenant, "INV", event.invoice_id), event.seq, event.model_dump_json()),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"audit seq {event.seq} already written") from exc

    def read(self, tenant: TenantId, invoice_id: str) -> Sequence[AuditEvent]:
        rows = self._conn.execute(
            "SELECT event_json FROM audit WHERE pk = ? ORDER BY seq",
            (entity_key(tenant, "INV", invoice_id),),
        ).fetchall()
        return [AuditEvent.model_validate_json(r[0]) for r in rows]
