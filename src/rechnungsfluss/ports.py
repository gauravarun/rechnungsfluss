"""Ports: the only way domain/pipeline code touches storage and services (ADR-0009).

Every method takes the tenant explicitly. Implementations must scope all
reads and writes to that tenant (ADR-0006).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from rechnungsfluss.domain.audit import AuditEvent
from rechnungsfluss.domain.duplicates import Fingerprint
from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.domain.matching import PurchaseOrder
from rechnungsfluss.domain.states import InvoiceState
from rechnungsfluss.domain.tenancy import TenantId
from rechnungsfluss.validation.kosit import ValidationResult


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
    meta: Mapping[str, object] = field(default_factory=dict)
    """JSON-serializable processing details: format, rule IDs, reasons, errors, receipts."""


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


class IdempotencyStore(Protocol):
    def claim(self, tenant: TenantId, key: str) -> bool:
        """Atomically record ``key``; True only for the first caller (ADR-0007)."""


class FingerprintStore(Protocol):
    def add(self, tenant: TenantId, fingerprint: Fingerprint) -> None: ...

    def for_seller(self, tenant: TenantId, seller_key: str) -> Sequence[Fingerprint]: ...


class PurchaseOrderRepository(Protocol):
    def get(self, tenant: TenantId, number: str) -> PurchaseOrder | None: ...


class Validator(Protocol):
    def validate(self, document: bytes) -> ValidationResult: ...


class Extractor(Protocol):
    """Turns a plain PDF into canonical invoice data (LLM-backed from W4)."""

    def extract(self, tenant: TenantId, pdf: bytes) -> Invoice: ...


class ExtractionUnavailableError(RuntimeError):
    """No extractor is configured; plain PDFs cannot be processed yet."""


@dataclass(frozen=True, slots=True)
class ErpReceipt:
    erp_document_id: str
    replayed: bool
    """True when the ERP recognised the idempotency key and returned the original result."""


class ErpUnavailableError(RuntimeError):
    """Transient ERP failure; the export can be retried with the same idempotency key."""


class ErpClient(Protocol):
    def post_invoice(
        self, tenant: TenantId, invoice_id: str, invoice: Invoice, idempotency_key: str
    ) -> ErpReceipt: ...
