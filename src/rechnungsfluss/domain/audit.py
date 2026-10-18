"""Append-only, hash-chained audit events (ADR-0005).

The chain gives tamper *evidence*: editing or deleting a stored event breaks
the hash of every later event of the same invoice. It is not tamper *proof*.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from rechnungsfluss.domain.tenancy import TenantId

GENESIS_HASH = "0" * 64


class AuditAction(StrEnum):
    STATE_CHANGED = "STATE_CHANGED"
    FIELD_CHANGED = "FIELD_CHANGED"
    REPLAYED = "REPLAYED"
    EXPORTED = "EXPORTED"


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant: str
    invoice_id: str
    seq: int = Field(ge=0)
    at: dt.datetime
    actor: str
    """User id or ``system:<component>``."""
    action: AuditAction
    field: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    reason: str | None = None
    prev_hash: str
    hash: str


def _canonical(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


def _event_hash(fields: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical(fields)).hexdigest()


def append_event(
    chain: Sequence[AuditEvent],
    *,
    tenant: TenantId,
    invoice_id: str,
    actor: str,
    action: AuditAction,
    at: dt.datetime,
    field: str | None = None,
    old_value: object = None,
    new_value: object = None,
    reason: str | None = None,
) -> AuditEvent:
    """Create the next event for ``chain`` (which must be one invoice's events, in order)."""
    if at.tzinfo is None:
        raise ValueError("audit timestamps must be timezone-aware")
    prev_hash = chain[-1].hash if chain else GENESIS_HASH
    body: dict[str, object] = {
        "tenant": tenant.value,
        "invoice_id": invoice_id,
        "seq": len(chain),
        "at": at.astimezone(dt.UTC).isoformat(),
        "actor": actor,
        "action": action.value,
        "field": field,
        "old_value": None if old_value is None else str(old_value),
        "new_value": None if new_value is None else str(new_value),
        "reason": reason,
        "prev_hash": prev_hash,
    }
    return AuditEvent.model_validate({**body, "hash": _event_hash(body)})


def verify_chain(chain: Iterable[AuditEvent]) -> bool:
    """True if every event's hash and back-link are intact and ``seq`` is contiguous."""
    prev = GENESIS_HASH
    for expected_seq, event in enumerate(chain):
        body = event.model_dump(mode="python", exclude={"hash"})
        body["at"] = event.at.astimezone(dt.UTC).isoformat()
        body["action"] = event.action.value
        if event.seq != expected_seq or event.prev_hash != prev:
            return False
        if _event_hash(body) != event.hash:
            return False
        prev = event.hash
    return True


def field_changes(
    before: Mapping[str, object], after: Mapping[str, object]
) -> list[tuple[str, object, object]]:
    """List ``(path, old, new)`` for every flattened field that differs."""
    paths = sorted(set(before) | set(after))
    return [(p, before.get(p), after.get(p)) for p in paths if before.get(p) != after.get(p)]
