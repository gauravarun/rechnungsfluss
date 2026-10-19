"""Local-mode adapters: filesystem object store, SQLite stores, in-memory ERP.

Used by ``RF_MODE=local`` and by tests. These enforce tenant isolation in
application code only; the IAM and KMS layers of ADR-0006 exist only in AWS.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path

from rechnungsfluss.domain.audit import AuditEvent
from rechnungsfluss.domain.duplicates import Fingerprint
from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.domain.matching import PurchaseOrder
from rechnungsfluss.domain.states import InvoiceState
from rechnungsfluss.domain.tenancy import TenantId, entity_key, tenant_prefix
from rechnungsfluss.ports import (
    ConflictError,
    ErpReceipt,
    ErpUnavailableError,
    InvoiceRecord,
    NotFoundError,
)


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
    invoice_json TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}'
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

CREATE TABLE IF NOT EXISTS idempotency (pk TEXT PRIMARY KEY);

CREATE TABLE IF NOT EXISTS fingerprints (
    pk TEXT NOT NULL,
    invoice_id TEXT NOT NULL,
    fingerprint_json TEXT NOT NULL,
    PRIMARY KEY (pk, invoice_id)
);

CREATE TABLE IF NOT EXISTS purchase_orders (pk TEXT PRIMARY KEY, po_json TEXT NOT NULL);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.executescript(_SCHEMA)
    return conn


def _row_to_record(row: tuple[str, str, int, str, str | None, str]) -> InvoiceRecord:
    invoice_id, state, version, sha, invoice_json, meta_json = row
    return InvoiceRecord(
        invoice_id=invoice_id,
        state=InvoiceState(state),
        version=version,
        document_sha256=sha,
        invoice=Invoice.model_validate_json(invoice_json) if invoice_json else None,
        meta=json.loads(meta_json),
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

    @staticmethod
    def _meta(record: InvoiceRecord) -> str:
        return json.dumps(dict(record.meta), sort_keys=True, default=str)

    def create(self, tenant: TenantId, record: InvoiceRecord) -> None:
        try:
            self._conn.execute(
                "INSERT INTO invoices VALUES (?, ?, ?, 0, ?, ?, ?)",
                (
                    self._pk(tenant, record.invoice_id),
                    tenant.value,
                    record.state.value,
                    record.document_sha256,
                    self._json(record),
                    self._meta(record),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"invoice {record.invoice_id} already exists") from exc

    def update(self, tenant: TenantId, record: InvoiceRecord, expected_version: int) -> None:
        cur = self._conn.execute(
            "UPDATE invoices SET state = ?, version = version + 1, invoice_json = ?, "
            "meta_json = ? WHERE pk = ? AND version = ?",
            (
                record.state.value,
                self._json(record),
                self._meta(record),
                self._pk(tenant, record.invoice_id),
                expected_version,
            ),
        )
        if cur.rowcount != 1:
            self.get(tenant, record.invoice_id)  # raises NotFoundError if absent
            raise ConflictError(f"version conflict on invoice {record.invoice_id}")

    def get(self, tenant: TenantId, invoice_id: str) -> InvoiceRecord:
        row = self._conn.execute(
            "SELECT ?, state, version, document_sha256, invoice_json, meta_json "
            "FROM invoices WHERE pk = ?",
            (invoice_id, self._pk(tenant, invoice_id)),
        ).fetchone()
        if row is None:
            raise NotFoundError(invoice_id)
        return _row_to_record(row)

    def list_by_state(self, tenant: TenantId, state: InvoiceState) -> Sequence[InvoiceRecord]:
        prefix = tenant_prefix(tenant) + "INV#"
        rows = self._conn.execute(
            "SELECT substr(pk, ?), state, version, document_sha256, invoice_json, meta_json "
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


def _key_digest(key: str) -> str:
    """Idempotency keys may contain any character; store a digest as the key component."""
    return hashlib.sha256(key.encode()).hexdigest()


class SqliteIdempotencyStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def claim(self, tenant: TenantId, key: str) -> bool:
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO idempotency VALUES (?)",
            (entity_key(tenant, "IDEM", _key_digest(key)),),
        )
        return cur.rowcount == 1


class SqliteFingerprintStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @staticmethod
    def _pk(tenant: TenantId, seller_key: str) -> str:
        return entity_key(tenant, "DUP", _key_digest(seller_key))

    def add(self, tenant: TenantId, fingerprint: Fingerprint) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO fingerprints VALUES (?, ?, ?)",
            (
                self._pk(tenant, fingerprint.seller_key),
                fingerprint.invoice_id,
                fingerprint.model_dump_json(),
            ),
        )

    def for_seller(self, tenant: TenantId, seller_key: str) -> Sequence[Fingerprint]:
        rows = self._conn.execute(
            "SELECT fingerprint_json FROM fingerprints WHERE pk = ? ORDER BY invoice_id",
            (self._pk(tenant, seller_key),),
        ).fetchall()
        return [Fingerprint.model_validate_json(r[0]) for r in rows]


class SqlitePurchaseOrderRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def load(self, orders: Iterable[PurchaseOrder]) -> int:
        count = 0
        for po in orders:
            tenant = TenantId(po.tenant)
            self._conn.execute(
                "INSERT OR REPLACE INTO purchase_orders VALUES (?, ?)",
                (entity_key(tenant, "PO", po.number), po.model_dump_json()),
            )
            count += 1
        return count

    def get(self, tenant: TenantId, number: str) -> PurchaseOrder | None:
        try:
            pk = entity_key(tenant, "PO", number)
        except ValueError:
            return None  # a number that cannot be a key cannot be one of our POs
        row = self._conn.execute(
            "SELECT po_json FROM purchase_orders WHERE pk = ?", (pk,)
        ).fetchone()
        return PurchaseOrder.model_validate_json(row[0]) if row else None


class InMemoryErp:
    """Stand-in for the W5 mock ERP: enforces idempotency keys, can inject failures."""

    def __init__(self) -> None:
        self.booked: dict[str, tuple[str, str]] = {}
        """idempotency key -> (tenant, invoice_id)"""
        self.fail_next = 0

    def post_invoice(
        self, tenant: TenantId, invoice_id: str, invoice: Invoice, idempotency_key: str
    ) -> ErpReceipt:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ErpUnavailableError("injected ERP failure")
        if idempotency_key in self.booked:
            return ErpReceipt(erp_document_id=_erp_id(idempotency_key), replayed=True)
        self.booked[idempotency_key] = (tenant.value, invoice_id)
        return ErpReceipt(erp_document_id=_erp_id(idempotency_key), replayed=False)


def _erp_id(key: str) -> str:
    return "ERP-" + hashlib.sha256(key.encode()).hexdigest()[:12].upper()


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
