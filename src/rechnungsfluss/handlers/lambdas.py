"""Lambda entry points. Thin: parse the event, call the pipeline, shape the output.

Wiring (AWS clients, tenant-scoped sessions) lives in ``handlers/runtime.py``;
tests pass a fake runtime to ``Handlers``. The module-level functions at the
bottom are what the Lambda configuration points at.

Event flow (infra/terraform/modules/pipeline):

    S3 <tenant>/inbox/<file> -> EventBridge -> SQS ingest (+DLQ) -> ingest()
      -> StartExecution(name = <tenant>-<invoice_id>)   # ingest idempotency, ADR-0007
      -> Step Functions: step(parse|validate|check|decide) -> Choice
           AUTO_APPROVED -> step(export)
           IN_REVIEW     -> await_review (waitForTaskToken) -> step(export) | end
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any, Protocol

from rechnungsfluss.application.pipeline import Pipeline, Step
from rechnungsfluss.domain.states import InvoiceState
from rechnungsfluss.domain.tenancy import InvalidTenantError, TenantId

log = logging.getLogger(__name__)

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
# Inbox keys are written by our upload API: <tenant>/inbox/<safe file name>. Anything
# else is rejected; this also avoids depending on how a key is URL-encoded in events.
_INBOX_KEY = re.compile(r"^(?P<tenant>[a-z][a-z0-9-]{2,31})/inbox/[A-Za-z0-9._-]{1,200}$")


class ExportFailedError(RuntimeError):
    """Raised so Step Functions retries the export with backoff (the key stays the same)."""


class UnknownTenantError(PermissionError):
    pass


class Runtime(Protocol):
    def known_tenants(self) -> frozenset[str]: ...

    def pipeline(self, tenant: TenantId) -> Pipeline: ...

    def read_inbox_object(self, tenant: TenantId, bucket: str, key: str) -> bytes: ...

    def start_execution(self, name: str, payload: Mapping[str, str]) -> bool:
        """Start the state machine at most once per name.

        Returns False when a *closed* execution with this name exists. For Standard
        workflows, AWS answers a repeated start of a still-running execution (same name
        and input) with the original execution, so that case returns True without
        creating a second one.
        """


class Handlers:
    def __init__(self, runtime: Runtime) -> None:
        self.rt = runtime

    def _tenant(self, value: object) -> TenantId:
        try:
            tenant = TenantId(str(value))
        except InvalidTenantError as exc:
            raise UnknownTenantError(str(value)) from exc
        if tenant.value not in self.rt.known_tenants():
            raise UnknownTenantError(tenant.value)
        return tenant

    def ingest(self, event: Mapping[str, Any]) -> dict[str, list[dict[str, str]]]:
        """SQS batch of EventBridge "Object Created" events; partial-batch failure response.

        A failed record returns to the queue and, after maxReceiveCount, lands in the DLQ
        (alarm + runbook). Unknown tenants and malformed keys fail the same way: they are
        operator problems, never silently dropped.
        """
        failures = []
        for record in event.get("Records", []):
            try:
                self._ingest_one(json.loads(record["body"]))
            except Exception:
                log.exception("ingest failed", extra={"message_id": record.get("messageId")})
                failures.append({"itemIdentifier": str(record.get("messageId"))})
        return {"batchItemFailures": failures}

    def _ingest_one(self, s3_event: Mapping[str, Any]) -> None:
        detail = s3_event["detail"]
        bucket, key = detail["bucket"]["name"], detail["object"]["key"]
        match = _INBOX_KEY.match(key)
        if match is None:
            raise ValueError("object key is not a tenant inbox key")
        if int(detail["object"].get("size", 0)) > MAX_DOCUMENT_BYTES:
            raise ValueError("document exceeds the size limit")
        tenant = self._tenant(match["tenant"])
        data = self.rt.read_inbox_object(tenant, bucket, key)
        outcome = self.rt.pipeline(tenant).ingest(tenant, data)
        if outcome.duplicate_delivery:
            log.info(
                "duplicate delivery",
                extra={"tenant": tenant.value, "invoice_id": outcome.invoice_id},
            )
        # Started even for a duplicate delivery: a crash between ingest and start must
        # not strand an invoice. The execution name makes the start itself idempotent.
        started = self.rt.start_execution(
            f"{tenant.value}-{outcome.invoice_id}",
            {"tenant": tenant.value, "invoice_id": outcome.invoice_id},
        )
        log.info(
            "execution",
            extra={"tenant": tenant.value, "invoice_id": outcome.invoice_id, "started": started},
        )

    def step(self, event: Mapping[str, Any]) -> dict[str, str]:
        tenant = self._tenant(event["tenant"])
        invoice_id = str(event["invoice_id"])
        step = Step(event["step"])
        rec = self.rt.pipeline(tenant).run_step(tenant, invoice_id, step)
        if step is Step.EXPORT and rec.state is InvoiceState.EXPORT_FAILED:
            raise ExportFailedError(str(rec.meta.get("error")))
        return {"tenant": tenant.value, "invoice_id": invoice_id, "state": rec.state.value}

    def await_review(self, event: Mapping[str, Any]) -> dict[str, str]:
        tenant = self._tenant(event["tenant"])
        invoice_id = str(event["invoice_id"])
        self.rt.pipeline(tenant).attach_review_token(tenant, invoice_id, str(event["task_token"]))
        return {"tenant": tenant.value, "invoice_id": invoice_id, "state": "IN_REVIEW"}


_handlers: Handlers | None = None


def _default() -> Handlers:
    global _handlers  # one runtime per Lambda container
    if _handlers is None:
        from rechnungsfluss.handlers.runtime import AwsRuntime

        _handlers = Handlers(AwsRuntime.from_env())
    return _handlers


def ingest_handler(event: Mapping[str, Any], context: object) -> dict[str, Any]:
    return _default().ingest(event)


def step_handler(event: Mapping[str, Any], context: object) -> dict[str, str]:
    return _default().step(event)


def await_review_handler(event: Mapping[str, Any], context: object) -> dict[str, str]:
    return _default().await_review(event)
