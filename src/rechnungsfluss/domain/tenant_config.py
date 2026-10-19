"""Per-tenant business configuration (ADR-0014).

The tolerance values are engineering placeholders awaiting the project owner's
confirmation (ADR-0014 status: proposed). They are not taken from a real AP policy.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from rechnungsfluss.domain.tenancy import TenantId


@dataclass(frozen=True, slots=True)
class MatchTolerances:
    price_pct: Decimal = Decimal("2")
    """Allowed net-price excess over the PO price, in percent."""
    price_abs: Decimal = Decimal("0.02")
    """Allowed absolute excess; the larger of the two tolerances applies."""
    quantity_pct: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class DuplicateTolerances:
    date_window: dt.timedelta = dt.timedelta(days=3)
    amount_abs: Decimal = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class TenantConfig:
    tenant: TenantId
    matching: MatchTolerances = MatchTolerances()
    duplicates: DuplicateTolerances = DuplicateTolerances()


_CONFIGS = {
    "nordwerk-de": TenantConfig(TenantId("nordwerk-de")),
    "nordwerk-at": TenantConfig(TenantId("nordwerk-at")),
}


class UnknownTenantError(LookupError):
    pass


def config_for(tenant: TenantId) -> TenantConfig:
    try:
        return _CONFIGS[tenant.value]
    except KeyError:
        raise UnknownTenantError(tenant.value) from None
