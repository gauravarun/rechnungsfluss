"""Tenant identity and tenant-scoped storage keys.

Every persisted key is built here, so the ``TENANT#<id>#`` prefix that IAM
``dynamodb:LeadingKeys`` policies rely on (ADR-0006) cannot be forgotten or
forged by a caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TENANT_RE = re.compile(r"^[a-z][a-z0-9-]{2,31}$")
# Key components must not contain the separator, or a crafted value such as
# "x#INV#other" could address another entity.
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")

KEY_SEPARATOR = "#"


class InvalidTenantError(ValueError):
    """Raised for malformed tenant IDs."""


class InvalidKeyComponentError(ValueError):
    """Raised when a key component could break out of its key segment."""


@dataclass(frozen=True, slots=True)
class TenantId:
    """A validated tenant identifier, e.g. ``nordwerk-de``."""

    value: str

    def __post_init__(self) -> None:
        if not _TENANT_RE.fullmatch(self.value):
            raise InvalidTenantError(f"invalid tenant id: {self.value!r}")

    def __str__(self) -> str:
        return self.value


def _component(value: str) -> str:
    if not _COMPONENT_RE.fullmatch(value):
        raise InvalidKeyComponentError(f"invalid key component: {value!r}")
    return value


def tenant_prefix(tenant: TenantId) -> str:
    return f"TENANT{KEY_SEPARATOR}{tenant.value}{KEY_SEPARATOR}"


def entity_key(tenant: TenantId, entity: str, *parts: str) -> str:
    """Build a partition key such as ``TENANT#nordwerk-de#INV#<id>``."""
    if not parts:
        raise InvalidKeyComponentError("entity key needs at least one id part")
    segments = [_component(entity), *(_component(p) for p in parts)]
    return tenant_prefix(tenant) + KEY_SEPARATOR.join(segments)


def tenant_of_key(key: str) -> TenantId:
    """Recover the tenant from a key built by :func:`entity_key`."""
    head, sep, rest = key.partition(KEY_SEPARATOR)
    tenant, sep2, _ = rest.partition(KEY_SEPARATOR)
    if head != "TENANT" or not sep or not sep2:
        raise InvalidKeyComponentError(f"not a tenant-scoped key: {key!r}")
    return TenantId(tenant)
