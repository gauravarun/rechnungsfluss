import pytest
from hypothesis import given
from hypothesis import strategies as st

from rechnungsfluss.domain.tenancy import (
    InvalidKeyComponentError,
    InvalidTenantError,
    TenantId,
    entity_key,
    tenant_of_key,
)

pytestmark = pytest.mark.tenancy


def test_entity_key_is_tenant_prefixed(tenant_de: TenantId) -> None:
    assert entity_key(tenant_de, "INV", "abc") == "TENANT#nordwerk-de#INV#abc"


@pytest.mark.parametrize("bad", ["", "ab", "Nordwerk", "nord werk", "x" * 40, "1abc", "a#b"])
def test_rejects_malformed_tenant_ids(bad: str) -> None:
    with pytest.raises(InvalidTenantError):
        TenantId(bad)


@pytest.mark.parametrize("bad", ["x#INV#other", "", "a b", "TENANT#nordwerk-at"])
def test_key_components_cannot_inject_separator(tenant_de: TenantId, bad: str) -> None:
    with pytest.raises(InvalidKeyComponentError):
        entity_key(tenant_de, "INV", bad)


def test_entity_key_requires_an_id(tenant_de: TenantId) -> None:
    with pytest.raises(InvalidKeyComponentError):
        entity_key(tenant_de, "INV")


def test_keys_of_different_tenants_never_collide(tenant_de: TenantId, tenant_at: TenantId) -> None:
    assert entity_key(tenant_de, "INV", "1") != entity_key(tenant_at, "INV", "1")


@given(st.from_regex(r"[A-Za-z0-9._:/-]{1,50}", fullmatch=True))
def test_tenant_round_trips_through_key(part: str) -> None:
    tenant = TenantId("nordwerk-at")
    assert tenant_of_key(entity_key(tenant, "DOC", part)) == tenant


@pytest.mark.parametrize("bad", ["INV#1", "TENANT", "TENANT#nordwerk-de"])
def test_tenant_of_key_rejects_unscoped_keys(bad: str) -> None:
    with pytest.raises(InvalidKeyComponentError):
        tenant_of_key(bad)
