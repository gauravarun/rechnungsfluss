"""AWS adapters: S3 and DynamoDB implementations of the storage ports (ADR-0005, ADR-0006).

Every adapter is built for **one tenant session**. In AWS, handlers obtain a
boto3 session by assuming the tenant-data role with session tag
``tenant=<id>``; that role's IAM policy only allows keys starting with
``TENANT#<id>#`` (DynamoDB ``LeadingKeys``) and objects under ``<id>/`` (S3).
The adapters additionally refuse calls for any other tenant, so a wiring bug
fails loudly instead of relying on IAM alone.

Table layout (see docs/architecture.md):

- ``main``: invoices (sk ``CURRENT``), POs (sk ``META``), fingerprints (sk ``INV#<id>``);
  ``gsi1`` lists invoices by state per tenant.
- ``audit``: append-only, ``PutItem`` guarded by ``attribute_not_exists``.
- ``idempotency``: claims with TTL.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

from rechnungsfluss.domain.audit import AuditEvent
from rechnungsfluss.domain.duplicates import Fingerprint
from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.domain.matching import PurchaseOrder
from rechnungsfluss.domain.states import InvoiceState
from rechnungsfluss.domain.tenancy import TenantId, entity_key, tenant_prefix
from rechnungsfluss.ports import ConflictError, InvoiceRecord, NotFoundError

if TYPE_CHECKING:
    from boto3.session import Session
    from mypy_boto3_dynamodb.service_resource import Table

IDEMPOTENCY_TTL_S = 90 * 24 * 3600
"""Matches the Step Functions execution-name dedupe window (ADR-0007)."""


class TenantMismatchError(PermissionError):
    """An adapter built for one tenant was called for another."""


def _conditional_failed(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class _TenantBound:
    def __init__(self, tenant: TenantId) -> None:
        self._tenant = tenant

    def _check(self, tenant: TenantId) -> None:
        if tenant != self._tenant:
            raise TenantMismatchError(f"adapter bound to {self._tenant}, called for {tenant}")


class S3ObjectStore(_TenantBound):
    def __init__(
        self, session: Session, tenant: TenantId, bucket: str, kms_key_id: str | None = None
    ) -> None:
        super().__init__(tenant)
        self._s3 = session.client("s3")
        self._bucket = bucket
        self._kms = kms_key_id

    def _key(self, key: str) -> str:
        if key.startswith("/") or ".." in key.split("/"):
            raise ValueError(f"object key escapes tenant scope: {key!r}")
        return f"{self._tenant.value}/{key}"

    def put(self, tenant: TenantId, key: str, data: bytes) -> None:
        self._check(tenant)
        extra: dict[str, str] = {}
        if self._kms:
            extra = {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": self._kms}
        self._s3.put_object(Bucket=self._bucket, Key=self._key(key), Body=data, **extra)  # type: ignore[arg-type]

    def get(self, tenant: TenantId, key: str) -> bytes:
        self._check(tenant)
        try:
            obj = self._s3.get_object(Bucket=self._bucket, Key=self._key(key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise NotFoundError(key) from exc
            raise
        body: bytes = obj["Body"].read()
        return body


class DynamoInvoiceRepository(_TenantBound):
    def __init__(self, session: Session, tenant: TenantId, table: str) -> None:
        super().__init__(tenant)
        self._t: Table = session.resource("dynamodb").Table(table)

    def _pk(self, invoice_id: str) -> str:
        return entity_key(self._tenant, "INV", invoice_id)

    def _item(self, record: InvoiceRecord, version: int) -> dict[str, Any]:
        return {
            "pk": self._pk(record.invoice_id),
            "sk": "CURRENT",
            "gsi1pk": f"{tenant_prefix(self._tenant)}STATE#{record.state.value}",
            "gsi1sk": record.invoice_id,
            "invoice_id": record.invoice_id,
            "state": record.state.value,
            "version": version,
            "sha": record.document_sha256,
            "invoice_json": record.invoice.model_dump_json() if record.invoice else None,
            "meta_json": json.dumps(dict(record.meta), sort_keys=True, default=str),
        }

    @staticmethod
    def _record(item: dict[str, Any]) -> InvoiceRecord:
        invoice_json = item.get("invoice_json")
        return InvoiceRecord(
            invoice_id=str(item["invoice_id"]),
            state=InvoiceState(item["state"]),
            version=int(item["version"]),
            document_sha256=str(item["sha"]),
            invoice=Invoice.model_validate_json(invoice_json) if invoice_json else None,
            meta=json.loads(str(item["meta_json"])),
        )

    def create(self, tenant: TenantId, record: InvoiceRecord) -> None:
        self._check(tenant)
        try:
            self._t.put_item(
                Item=self._item(record, 0), ConditionExpression="attribute_not_exists(pk)"
            )
        except ClientError as exc:
            if _conditional_failed(exc):
                raise ConflictError(f"invoice {record.invoice_id} already exists") from exc
            raise

    def update(self, tenant: TenantId, record: InvoiceRecord, expected_version: int) -> None:
        self._check(tenant)
        try:
            self._t.put_item(
                Item=self._item(record, expected_version + 1),
                ConditionExpression="attribute_exists(pk) AND version = :v",
                ExpressionAttributeValues={":v": expected_version},
            )
        except ClientError as exc:
            if _conditional_failed(exc):
                self.get(tenant, record.invoice_id)  # NotFoundError if absent
                raise ConflictError(f"version conflict on invoice {record.invoice_id}") from exc
            raise

    def get(self, tenant: TenantId, invoice_id: str) -> InvoiceRecord:
        self._check(tenant)
        resp = self._t.get_item(
            Key={"pk": self._pk(invoice_id), "sk": "CURRENT"}, ConsistentRead=True
        )
        if "Item" not in resp:
            raise NotFoundError(invoice_id)
        return self._record(resp["Item"])

    def list_by_state(self, tenant: TenantId, state: InvoiceState) -> Sequence[InvoiceRecord]:
        self._check(tenant)
        resp = self._t.query(
            IndexName="gsi1",
            KeyConditionExpression="gsi1pk = :p",
            ExpressionAttributeValues={":p": f"{tenant_prefix(tenant)}STATE#{state.value}"},
        )
        return [self._record(item) for item in resp.get("Items", [])]


class DynamoAuditLog(_TenantBound):
    def __init__(self, session: Session, tenant: TenantId, table: str) -> None:
        super().__init__(tenant)
        self._t: Table = session.resource("dynamodb").Table(table)

    def append(self, tenant: TenantId, event: AuditEvent) -> None:
        self._check(tenant)
        if event.tenant != tenant.value:
            raise ValueError("event tenant does not match caller tenant")
        try:
            self._t.put_item(
                Item={
                    "pk": entity_key(tenant, "INV", event.invoice_id),
                    "seq": event.seq,
                    "event_json": event.model_dump_json(),
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as exc:
            if _conditional_failed(exc):
                raise ConflictError(f"audit seq {event.seq} already written") from exc
            raise

    def read(self, tenant: TenantId, invoice_id: str) -> Sequence[AuditEvent]:
        self._check(tenant)
        resp = self._t.query(
            KeyConditionExpression="pk = :p",
            ExpressionAttributeValues={":p": entity_key(tenant, "INV", invoice_id)},
            ConsistentRead=True,
        )
        return [AuditEvent.model_validate_json(str(i["event_json"])) for i in resp.get("Items", [])]


class DynamoIdempotencyStore(_TenantBound):
    def __init__(self, session: Session, tenant: TenantId, table: str) -> None:
        super().__init__(tenant)
        self._t: Table = session.resource("dynamodb").Table(table)

    def claim(self, tenant: TenantId, key: str) -> bool:
        self._check(tenant)
        try:
            self._t.put_item(
                Item={
                    "id": entity_key(tenant, "IDEM", _digest(key)),
                    "expiration": int(time.time()) + IDEMPOTENCY_TTL_S,
                },
                ConditionExpression="attribute_not_exists(id)",
            )
        except ClientError as exc:
            if _conditional_failed(exc):
                return False
            raise
        return True


class DynamoFingerprintStore(_TenantBound):
    def __init__(self, session: Session, tenant: TenantId, table: str) -> None:
        super().__init__(tenant)
        self._t: Table = session.resource("dynamodb").Table(table)

    def add(self, tenant: TenantId, fingerprint: Fingerprint) -> None:
        self._check(tenant)
        self._t.put_item(
            Item={
                "pk": entity_key(tenant, "DUP", _digest(fingerprint.seller_key)),
                "sk": f"INV#{fingerprint.invoice_id}",
                "fingerprint_json": fingerprint.model_dump_json(),
            }
        )

    def for_seller(self, tenant: TenantId, seller_key: str) -> Sequence[Fingerprint]:
        self._check(tenant)
        resp = self._t.query(
            KeyConditionExpression="pk = :p",
            ExpressionAttributeValues={":p": entity_key(tenant, "DUP", _digest(seller_key))},
        )
        return [
            Fingerprint.model_validate_json(str(i["fingerprint_json"]))
            for i in resp.get("Items", [])
        ]


class DynamoPurchaseOrderRepository(_TenantBound):
    def __init__(self, session: Session, tenant: TenantId, table: str) -> None:
        super().__init__(tenant)
        self._t: Table = session.resource("dynamodb").Table(table)

    def put(self, tenant: TenantId, po: PurchaseOrder) -> None:
        self._check(tenant)
        if po.tenant != tenant.value:
            raise TenantMismatchError("purchase order belongs to another tenant")
        self._t.put_item(
            Item={
                "pk": entity_key(tenant, "PO", po.number),
                "sk": "META",
                "po_json": po.model_dump_json(),
            }
        )

    def get(self, tenant: TenantId, number: str) -> PurchaseOrder | None:
        self._check(tenant)
        try:
            pk = entity_key(tenant, "PO", number)
        except ValueError:
            return None
        resp = self._t.get_item(Key={"pk": pk, "sk": "META"})
        item = resp.get("Item")
        return PurchaseOrder.model_validate_json(str(item["po_json"])) if item else None
