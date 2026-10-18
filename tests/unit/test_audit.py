import datetime as dt

import pytest

from rechnungsfluss.domain.audit import (
    GENESIS_HASH,
    AuditAction,
    AuditEvent,
    append_event,
    field_changes,
    verify_chain,
)
from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.domain.tenancy import TenantId
from tests.conftest import make_invoice

T0 = dt.datetime(2026, 10, 18, 9, 0, tzinfo=dt.UTC)


def _chain(tenant: TenantId, n: int = 3) -> list[AuditEvent]:
    chain: list[AuditEvent] = []
    for i in range(n):
        chain.append(
            append_event(
                chain,
                tenant=tenant,
                invoice_id="inv-1",
                actor="user:clerk-1",
                action=AuditAction.FIELD_CHANGED,
                at=T0 + dt.timedelta(minutes=i),
                field="totals.amount_due",
                old_value=f"{i}.00",
                new_value=f"{i + 1}.00",
                reason="typo",
            )
        )
    return chain


def test_chain_links_and_verifies(tenant_de: TenantId) -> None:
    chain = _chain(tenant_de)
    assert chain[0].prev_hash == GENESIS_HASH
    assert chain[1].prev_hash == chain[0].hash
    assert [e.seq for e in chain] == [0, 1, 2]
    assert verify_chain(chain)


def test_tampered_value_breaks_chain(tenant_de: TenantId) -> None:
    chain = _chain(tenant_de)
    chain[1] = chain[1].model_copy(update={"new_value": "9999.00"})
    assert not verify_chain(chain)


def test_deleted_event_breaks_chain(tenant_de: TenantId) -> None:
    chain = _chain(tenant_de)
    del chain[1]
    assert not verify_chain(chain)


def test_reordered_events_break_chain(tenant_de: TenantId) -> None:
    chain = _chain(tenant_de)
    chain[1], chain[2] = chain[2], chain[1]
    assert not verify_chain(chain)


def test_naive_timestamps_are_rejected(tenant_de: TenantId) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        append_event(
            [],
            tenant=tenant_de,
            invoice_id="inv-1",
            actor="system:test",
            action=AuditAction.STATE_CHANGED,
            at=dt.datetime(2026, 10, 18, 9, 0),  # noqa: DTZ001
        )


def test_hash_is_timezone_independent(tenant_de: TenantId) -> None:
    cet = dt.timezone(dt.timedelta(hours=2))
    kwargs = {
        "tenant": tenant_de,
        "invoice_id": "inv-1",
        "actor": "system:test",
        "action": AuditAction.STATE_CHANGED,
    }
    a = append_event([], at=T0, **kwargs)  # type: ignore[arg-type]
    b = append_event([], at=T0.astimezone(cet), **kwargs)  # type: ignore[arg-type]
    assert a.hash == b.hash
    assert verify_chain([b])


def test_field_changes_reports_only_differences(invoice: Invoice) -> None:
    corrected = make_invoice(number="RE-2026-0001-K", purchase_order_ref=None)
    changes = field_changes(invoice.field_paths(), corrected.field_paths())
    assert changes == [
        ("number", "RE-2026-0001", "RE-2026-0001-K"),
        ("purchase_order_ref", "PO-DE-1001", None),
    ]
