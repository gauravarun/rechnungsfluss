"""Shared fixtures for integration tests.

KoSIT tests are skipped locally when the daemon is not running. CI sets
RF_REQUIRE_KOSIT=1 so a missing daemon fails the build instead of skipping.
"""

from __future__ import annotations

import os

import pytest

from rechnungsfluss.validation.kosit import KositValidator, ValidatorUnavailableError

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
