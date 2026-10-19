"""Test doubles shared by unit tests."""

from __future__ import annotations

from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.domain.tenancy import TenantId
from rechnungsfluss.validation.kosit import Finding, Severity, ValidationResult


class StubValidator:
    def __init__(self) -> None:
        self.calls = 0

    def validate(self, document: bytes) -> ValidationResult:
        self.calls += 1
        if b"999.99" in document:
            return ValidationResult(
                accepted=False,
                scenario="stub",
                findings=(Finding(code="STUB-1", severity=Severity.ERROR, message="bad total"),),
            )
        return ValidationResult(accepted=True, scenario="stub")


class FixedExtractor:
    def __init__(self, invoice: Invoice) -> None:
        self.invoice = invoice

    def extract(self, tenant: TenantId, pdf: bytes) -> Invoice:
        return self.invoice
