"""Ports: the only way domain/pipeline code touches storage (ADR-0009).

Every method takes the tenant explicitly. Implementations must scope all
reads and writes to that tenant (ADR-0006). Further ports (queue, extractor,
validator, ERP client) are added with the features that need them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from rechnungsfluss.domain.audit import AuditEvent
from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.domain.states import InvoiceState
from rechnungsfluss.domain.tenancy import TenantId


class NotFoundError(LookupError):
    """The item does not exist *for this tenant* (other tenants' data looks absent)."""


class ConflictError(RuntimeError):
    """Optimistic-concurrency or append-only violation."""


@dataclass(frozen=True, slots=True)
class InvoiceRecord:
    invoice_id: str
    state: InvoiceState
    version: int
    document_sha256: str
    invoice: Invoice | None = None


class ObjectStore(Protocol):
    def put(self, tenant: TenantId, key: str, data: bytes) -> None: ...

    def get(self, tenant: TenantId, key: str) -> bytes: ...


class InvoiceRepository(Protocol):
    def create(self, tenant: TenantId, record: InvoiceRecord) -> None:
        """Insert version 0. Raises ConflictError if the id already exists."""

    def update(self, tenant: TenantId, record: InvoiceRecord, expected_version: int) -> None:
        """Replace the current record if its version equals ``expected_version``."""

    def get(self, tenant: TenantId, invoice_id: str) -> InvoiceRecord: ...

    def list_by_state(self, tenant: TenantId, state: InvoiceState) -> Sequence[InvoiceRecord]: ...


class AuditLog(Protocol):
    """Append-only: there is deliberately no update or delete."""

    def append(self, tenant: TenantId, event: AuditEvent) -> None: ...

    def read(self, tenant: TenantId, invoice_id: str) -> Sequence[AuditEvent]: ...
