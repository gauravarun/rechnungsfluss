"""Invoice pipeline: the same steps run in-process locally and as Lambdas in AWS.

    ingest -> parse -> validate -> check (duplicates + 2-way match) -> decide
           -> AUTO_APPROVED -> export          (no reasons)
           -> IN_REVIEW -> approve/reject      (any reason)

Every state change goes through ``_transition``: it checks the allowed
transitions, writes with optimistic concurrency and appends a hash-chained
audit event. Steps are guarded by the current state, so calling ``process``
or ``export`` again is a no-op instead of double work (ADR-0007). In AWS the
Step Functions execution name and Powertools idempotency add the other layers.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from rechnungsfluss.domain.audit import AuditAction, append_event, field_changes
from rechnungsfluss.domain.duplicates import DuplicateHit, find_duplicates, fingerprint
from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.domain.matching import MatchIssue, match
from rechnungsfluss.domain.reasons import Reason
from rechnungsfluss.domain.routing import Route, decide
from rechnungsfluss.domain.states import InvoiceState, transition
from rechnungsfluss.domain.tenancy import TenantId
from rechnungsfluss.domain.tenant_config import config_for
from rechnungsfluss.formats import SPEC_EN16931, cii, ubl, zugferd
from rechnungsfluss.formats.classify import Syntax, detect
from rechnungsfluss.formats.common import ParseError
from rechnungsfluss.ports import (
    AuditLog,
    ErpClient,
    ErpUnavailableError,
    ExtractionUnavailableError,
    Extractor,
    FingerprintStore,
    IdempotencyStore,
    InvoiceRecord,
    InvoiceRepository,
    ObjectStore,
    PurchaseOrderRepository,
    Validator,
)

S = InvoiceState
SYSTEM = "system:pipeline"
log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Services:
    store: ObjectStore
    repo: InvoiceRepository
    audit: AuditLog
    idempotency: IdempotencyStore
    fingerprints: FingerprintStore
    purchase_orders: PurchaseOrderRepository
    validator: Validator
    erp: ErpClient
    extractor: Extractor | None
    clock: Callable[[], dt.datetime]


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    invoice_id: str
    duplicate_delivery: bool
    """True when these exact bytes were already ingested; nothing new was created."""


class ReviewActionError(ValueError):
    """The requested review action is not allowed for this invoice."""


# Fields a reviewer may correct. Everything else needs a corrected document from the supplier.
CORRECTABLE_PREFIXES = (
    "number",
    "issue_date",
    "due_date",
    "buyer_reference",
    "purchase_order_ref",
    "seller.vat_id",
    "seller_iban",
    "totals.",
    "lines[",
    "vat_breakdown[",
)


class Pipeline:
    def __init__(self, services: Services) -> None:
        self.s = services

    # --- ingest ------------------------------------------------------------------

    def ingest(self, tenant: TenantId, data: bytes) -> IngestOutcome:
        sha = hashlib.sha256(data).hexdigest()
        invoice_id = sha[:24]
        if not self.s.idempotency.claim(tenant, f"ingest:{sha}"):
            log.info(
                "duplicate delivery ignored",
                extra={"tenant": tenant.value, "invoice_id": invoice_id},
            )
            return IngestOutcome(invoice_id=invoice_id, duplicate_delivery=True)
        self.s.store.put(tenant, f"documents/{sha}", data)
        record = InvoiceRecord(
            invoice_id=invoice_id, state=S.RECEIVED, version=0, document_sha256=sha
        )
        self.s.repo.create(tenant, record)
        self._audit(tenant, invoice_id, AuditAction.STATE_CHANGED, new_value=S.RECEIVED.value)
        return IngestOutcome(invoice_id=invoice_id, duplicate_delivery=False)

    def process(self, tenant: TenantId, invoice_id: str) -> InvoiceRecord:
        """Run every automatic step that the current state allows."""
        rec = self.s.repo.get(tenant, invoice_id)
        if rec.state is S.RECEIVED:
            rec = self._parse(tenant, rec)
        if rec.state is S.PARSED:
            rec = self._validate(tenant, rec)
        if rec.state is S.VALIDATED:
            rec = self._check(tenant, rec)
        if rec.state is S.MATCHED:
            rec = self._decide(tenant, rec)
        if rec.state is S.AUTO_APPROVED:
            rec = self.export(tenant, invoice_id)
        return rec

    # --- steps ---------------------------------------------------------------------

    def _parse(self, tenant: TenantId, rec: InvoiceRecord) -> InvoiceRecord:
        data = self.s.store.get(tenant, f"documents/{rec.document_sha256}")
        try:
            syntax = detect(data)
            invoice: Invoice | None
            is_e_invoice = True
            profile: str | None = None
            if syntax is Syntax.CII:
                invoice, xml, fmt = cii.from_cii(data), data, "cii"
            elif syntax is Syntax.UBL:
                invoice, xml, fmt = ubl.from_ubl(data), data, "ubl"
            else:
                try:
                    doc = zugferd.extract(data)
                    invoice, xml, fmt = doc.invoice, doc.xml, "zugferd"
                    is_e_invoice, profile = doc.is_e_invoice, doc.profile.value
                except zugferd.NotZugferdError:
                    invoice, fmt = self._extract(tenant, data), "pdf"
                    xml = cii.to_cii(invoice, specification_id=SPEC_EN16931)
        except (ParseError, ExtractionUnavailableError) as exc:
            return self._transition(tenant, rec, S.FAILED, meta={"error": str(exc)})
        self.s.store.put(tenant, f"validation-input/{rec.invoice_id}.xml", xml)
        meta = {"format": fmt, "is_e_invoice": is_e_invoice, "profile": profile}
        return self._transition(tenant, rec, S.PARSED, invoice=invoice, meta=meta)

    def _extract(self, tenant: TenantId, pdf: bytes) -> Invoice:
        if self.s.extractor is None:
            raise ExtractionUnavailableError("plain PDF received but no extractor is configured")
        return self.s.extractor.extract(tenant, pdf)

    def _validate(self, tenant: TenantId, rec: InvoiceRecord) -> InvoiceRecord:
        if not rec.meta.get("is_e_invoice", True):
            return self._transition(tenant, rec, S.VALIDATED, meta={"validation": None})
        xml = self.s.store.get(tenant, f"validation-input/{rec.invoice_id}.xml")
        result = self.s.validator.validate(xml)  # ValidatorUnavailableError -> step retry
        meta = {
            "validation": {
                "accepted": result.accepted,
                "scenario": result.scenario,
                "rule_ids": sorted(result.error_codes),
            }
        }
        return self._transition(tenant, rec, S.VALIDATED, meta=meta)

    def _check(self, tenant: TenantId, rec: InvoiceRecord) -> InvoiceRecord:
        cfg = config_for(tenant)
        inv = rec.invoice
        meta: dict[str, object] = {"match_issues": [], "duplicate_of": []}
        if inv is not None:
            fp = fingerprint(rec.invoice_id, inv)
            hits = find_duplicates(
                fp, self.s.fingerprints.for_seller(tenant, fp.seller_key), cfg.duplicates
            )
            self.s.fingerprints.add(tenant, fp)
            po = (
                self.s.purchase_orders.get(tenant, inv.purchase_order_ref)
                if inv.purchase_order_ref
                else None
            )
            issues = match(inv, po, cfg.matching)
            meta = {
                "match_issues": [i.model_dump(mode="json") for i in issues],
                "duplicate_of": [h.model_dump(mode="json") for h in hits],
            }
        return self._transition(tenant, rec, S.MATCHED, meta=meta)

    def _decide(self, tenant: TenantId, rec: InvoiceRecord) -> InvoiceRecord:
        validation = rec.meta.get("validation")
        accepted = validation.get("accepted") if isinstance(validation, dict) else None
        decision = decide(
            invoice=rec.invoice,
            is_e_invoice=bool(rec.meta.get("is_e_invoice", True)),
            validation_accepted=accepted,
            match_issues=[MatchIssue.model_validate(i) for i in _list(rec.meta, "match_issues")],
            duplicates=[DuplicateHit.model_validate(h) for h in _list(rec.meta, "duplicate_of")],
        )
        target = S.AUTO_APPROVED if decision.route is Route.AUTO_APPROVE else S.IN_REVIEW
        return self._transition(
            tenant, rec, target, meta={"reasons": [r.value for r in decision.reasons]}
        )

    # --- review actions ----------------------------------------------------------------

    def approve(
        self,
        tenant: TenantId,
        invoice_id: str,
        *,
        actor: str,
        corrections: Mapping[str, str] | None = None,
    ) -> InvoiceRecord:
        rec = self._require(tenant, invoice_id, S.IN_REVIEW)
        if rec.invoice is None:
            raise ReviewActionError(
                "no invoice data (e.g. Factur-X MINIMUM); reject and request an e-invoice"
            )
        invoice = rec.invoice
        meta: dict[str, object] = {}
        if corrections:
            invoice = self._apply_corrections(tenant, rec, dict(corrections), actor)
            meta = {
                "content_version": int(_int(rec.meta, "content_version")) + 1,
                "corrected_by": actor,
            }
        else:
            validation = rec.meta.get("validation")
            if isinstance(validation, dict) and validation.get("accepted") is False:
                raise ReviewActionError(
                    "document failed official validation; correct the fields or reject it"
                )
        self._transition(
            tenant,
            rec,
            S.APPROVED,
            invoice=invoice,
            actor=actor,
            meta={**meta, "approved_by": actor},
        )
        return self.export(tenant, invoice_id)

    def reject(
        self, tenant: TenantId, invoice_id: str, *, actor: str, reason: str
    ) -> InvoiceRecord:
        if not reason.strip():
            raise ReviewActionError("a rejection needs a reason")
        rec = self._require(tenant, invoice_id, S.IN_REVIEW)
        return self._transition(
            tenant,
            rec,
            S.REJECTED,
            actor=actor,
            reason=reason,
            meta={"rejected_by": actor, "rejection_reason": reason},
        )

    def _apply_corrections(
        self, tenant: TenantId, rec: InvoiceRecord, corrections: dict[str, str], actor: str
    ) -> Invoice:
        if rec.invoice is None:  # approve() checks this; kept as an explicit invariant
            raise ReviewActionError("no invoice data to correct")
        for path in corrections:
            if not path.startswith(CORRECTABLE_PREFIXES):
                raise ReviewActionError(f"field {path!r} cannot be corrected by a reviewer")
        data: dict[str, Any] = rec.invoice.model_dump(mode="json")
        for path, value in corrections.items():
            _set_path(data, path, value)
        try:
            corrected = Invoice.model_validate(data)
        except ValueError as exc:
            raise ReviewActionError(f"correction does not fit the invoice model: {exc}") from exc
        spec = corrected.specification_id or SPEC_EN16931
        result = self.s.validator.validate(cii.to_cii(corrected, specification_id=spec))
        if not result.accepted:
            raise ReviewActionError(
                f"corrected invoice still fails validation: {sorted(result.error_codes)}"
            )
        for path, old, new in field_changes(rec.invoice.field_paths(), corrected.field_paths()):
            self._audit(
                tenant,
                rec.invoice_id,
                AuditAction.FIELD_CHANGED,
                actor=actor,
                field=path,
                old_value=old,
                new_value=new,
                reason="reviewer correction",
            )
        return corrected

    # --- export and replay ---------------------------------------------------------------

    def export(self, tenant: TenantId, invoice_id: str) -> InvoiceRecord:
        rec = self.s.repo.get(tenant, invoice_id)
        if rec.state not in (S.AUTO_APPROVED, S.APPROVED, S.EXPORT_FAILED):
            return rec  # already exported, or not approved: nothing to do
        if rec.invoice is None:
            raise RuntimeError(f"approved invoice {invoice_id} has no invoice data")
        key = f"{tenant.value}:{invoice_id}:v{_int(rec.meta, 'content_version')}"
        try:
            receipt = self.s.erp.post_invoice(tenant, invoice_id, rec.invoice, key)
        except ErpUnavailableError as exc:
            return self._transition(tenant, rec, S.EXPORT_FAILED, meta={"error": str(exc)})
        rec = self._transition(
            tenant,
            rec,
            S.EXPORTED,
            meta={"erp_document_id": receipt.erp_document_id, "error": None},
        )
        self._audit(tenant, invoice_id, AuditAction.EXPORTED, new_value=receipt.erp_document_id)
        return rec

    def replay(self, tenant: TenantId, invoice_id: str, *, actor: str) -> InvoiceRecord:
        """Re-drive a FAILED or EXPORT_FAILED invoice (the replay tool calls this)."""
        rec = self.s.repo.get(tenant, invoice_id)
        if rec.state is S.EXPORT_FAILED:
            self._audit(tenant, invoice_id, AuditAction.REPLAYED, actor=actor)
            return self.export(tenant, invoice_id)
        if rec.state is S.FAILED:
            self._audit(tenant, invoice_id, AuditAction.REPLAYED, actor=actor)
            rec = self._transition(tenant, rec, S.RECEIVED, actor=actor, meta={"error": None})
            return self.process(tenant, invoice_id)
        raise ReviewActionError(f"invoice in state {rec.state} cannot be replayed")

    # --- plumbing ------------------------------------------------------------------------

    def _require(self, tenant: TenantId, invoice_id: str, state: InvoiceState) -> InvoiceRecord:
        rec = self.s.repo.get(tenant, invoice_id)
        if rec.state is not state:
            raise ReviewActionError(f"invoice is {rec.state}, expected {state}")
        return rec

    def _transition(
        self,
        tenant: TenantId,
        rec: InvoiceRecord,
        target: InvoiceState,
        *,
        actor: str = SYSTEM,
        invoice: Invoice | None = None,
        meta: Mapping[str, object] | None = None,
        reason: str | None = None,
    ) -> InvoiceRecord:
        transition(rec.state, target)
        updated = replace(
            rec,
            state=target,
            version=rec.version + 1,
            invoice=invoice if invoice is not None else rec.invoice,
            meta={**rec.meta, **(meta or {})},
        )
        self.s.repo.update(tenant, updated, expected_version=rec.version)
        self._audit(
            tenant,
            rec.invoice_id,
            AuditAction.STATE_CHANGED,
            actor=actor,
            old_value=rec.state.value,
            new_value=target.value,
            reason=reason,
        )
        return updated

    def _audit(
        self,
        tenant: TenantId,
        invoice_id: str,
        action: AuditAction,
        *,
        actor: str = SYSTEM,
        field: str | None = None,
        old_value: object = None,
        new_value: object = None,
        reason: str | None = None,
    ) -> None:
        chain = list(self.s.audit.read(tenant, invoice_id))
        event = append_event(
            chain,
            tenant=tenant,
            invoice_id=invoice_id,
            actor=actor,
            action=action,
            at=self.s.clock(),
            field=field,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
        )
        self.s.audit.append(tenant, event)


def _list(meta: Mapping[str, object], key: str) -> list[Any]:
    value = meta.get(key)
    return list(value) if isinstance(value, list) else []


def _int(meta: Mapping[str, object], key: str) -> int:
    value = meta.get(key, 0)
    return value if isinstance(value, int) else 0


def _set_path(data: dict[str, Any], path: str, value: str) -> None:
    """Set ``lines[0].quantity``-style paths inside a model_dump() dict."""
    parts: list[str | int] = []
    for segment in path.split("."):
        name, _, rest = segment.partition("[")
        if name:
            parts.append(name)
        while rest:
            index, _, rest = rest.partition("]")
            parts.append(int(index))
            rest = rest.removeprefix("[")
    target: Any = data
    try:
        for part in parts[:-1]:
            target = target[part]
        if isinstance(target, dict) and parts[-1] not in target:
            raise KeyError(parts[-1])
        target[parts[-1]] = value
    except (KeyError, IndexError, TypeError) as exc:
        raise ReviewActionError(f"unknown field {path!r}") from exc


__all__ = ["IngestOutcome", "Pipeline", "Reason", "ReviewActionError", "Services"]
