"""Runs real invoices through the real KoSIT validator daemon.

Locally these are skipped when the daemon is not running. CI sets
RF_REQUIRE_KOSIT=1 so a missing daemon fails the build instead of skipping.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings

from rechnungsfluss.domain.invoice import Invoice, Totals
from rechnungsfluss.formats import SPEC_EN16931, SPEC_XRECHNUNG_3_0, cii
from rechnungsfluss.validation.kosit import KositValidator, ValidatorUnavailableError
from tests.conftest import make_invoice
from tests.strategies import invoices

pytestmark = pytest.mark.kosit

KOSIT_URL = os.environ.get("RF_KOSIT_URL", "http://127.0.0.1:8081")


@pytest.fixture(scope="module")
def validator() -> KositValidator:
    v = KositValidator(KOSIT_URL, timeout_s=60)
    try:
        v.validate(b"<probe/>")
    except ValidatorUnavailableError:
        if os.environ.get("RF_REQUIRE_KOSIT") == "1":
            pytest.fail(f"KoSIT daemon not reachable at {KOSIT_URL}")
        pytest.skip(f"KoSIT daemon not reachable at {KOSIT_URL}")
    return v


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(inv=invoices())
def test_generated_invoices_pass_xrechnung(validator: KositValidator, inv: Invoice) -> None:
    result = validator.validate(cii.to_cii(inv, specification_id=SPEC_XRECHNUNG_3_0))
    assert result.scenario == "EN16931 XRechnung (CII)"
    assert result.accepted, sorted(result.error_codes)


def test_plain_en16931_scenario_is_selected_by_bt24(validator: KositValidator) -> None:
    result = validator.validate(cii.to_cii(make_invoice(), specification_id=SPEC_EN16931))
    assert result.scenario == "EN16931 (CII)"
    assert result.accepted


def test_wrong_grand_total_fails_br_co_15(validator: KositValidator) -> None:
    inv = make_invoice(
        totals=Totals(
            line_net_sum=Decimal("125.00"),
            total_without_vat=Decimal("125.00"),
            vat_total=Decimal("23.75"),
            total_with_vat=Decimal("150.00"),
            amount_due=Decimal("150.00"),
        )
    )
    result = validator.validate(cii.to_cii(inv, specification_id=SPEC_EN16931))
    assert not result.accepted
    assert "BR-CO-15" in result.error_codes


def test_missing_buyer_reference_only_fails_under_xrechnung(validator: KositValidator) -> None:
    inv = make_invoice(buyer_reference=None)
    xr = validator.validate(cii.to_cii(inv, specification_id=SPEC_XRECHNUNG_3_0))
    en = validator.validate(cii.to_cii(inv, specification_id=SPEC_EN16931))
    assert "BR-DE-15" in xr.error_codes
    assert en.accepted
