"""Client for the KoSIT validator daemon (ADR-0003).

The daemon picks the validation scenario itself from the document: XRechnung
rules when BT-24 names XRechnung, plain EN 16931 rules otherwise. We only read
its verdict and messages. No rule is evaluated in Python.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import StrEnum

from lxml import etree

from rechnungsfluss.formats.common import parse_xml

REP = "http://www.xoev.de/de/validator/varl/1"
SCN = "http://www.xoev.de/de/validator/framework/1/scenarios"
NS = {"rep": REP, "s": SCN}


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFORMATION = "information"


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    """Official rule ID, e.g. ``BR-CO-15`` or ``BR-DE-1``."""
    severity: Severity
    message: str
    location: str | None = None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    accepted: bool
    scenario: str | None
    """Name of the KoSIT scenario that matched, None if none matched."""
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    @property
    def error_codes(self) -> frozenset[str]:
        return frozenset(f.code for f in self.findings if f.severity is Severity.ERROR)


class ValidatorUnavailableError(RuntimeError):
    """The daemon could not be reached; the caller should retry, not reject the invoice."""


def parse_report(report: bytes) -> ValidationResult:
    root = parse_xml(report)
    scenario_el = root.find("rep:scenarioMatched/s:scenario/s:name", NS)
    findings = []
    for msg in root.iter(f"{{{REP}}}message"):
        level = msg.get("level", "error")
        severity = Severity(level) if level in Severity._value2member_map_ else Severity.ERROR
        findings.append(
            Finding(
                code=msg.get("code") or msg.get("id") or "UNKNOWN",
                severity=severity,
                message=" ".join("".join(str(t) for t in msg.itertext()).split()),
                location=msg.get("xpathLocation"),
            )
        )
    accepted = root.find("rep:assessment/rep:accept", NS) is not None
    return ValidationResult(
        accepted=accepted,
        scenario=scenario_el.text.strip() if scenario_el is not None and scenario_el.text else None,
        findings=tuple(findings),
    )


class KositValidator:
    def __init__(self, base_url: str, timeout_s: float = 30.0) -> None:
        if not base_url.startswith(("http://127.0.0.1", "http://localhost", "https://")):
            # Plain HTTP only to a local daemon; anything remote must use TLS.
            raise ValueError("KoSIT URL must be a local http:// address or https://")
        self._url = base_url.rstrip("/") + "/"
        self._timeout = timeout_s

    def validate(self, document: bytes) -> ValidationResult:
        request = urllib.request.Request(  # noqa: S310 (scheme checked in __init__)
            self._url,
            data=document,
            method="POST",
            headers={"Content-Type": "application/xml"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:  # noqa: S310
                body: bytes = resp.read()
        except urllib.error.HTTPError as exc:
            # The daemon answers rejected documents with a non-2xx status and a full report.
            body = exc.read()
            if not body.lstrip().startswith(b"<"):
                raise ValidatorUnavailableError(f"validator HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ValidatorUnavailableError(str(exc)) from exc
        try:
            return parse_report(body)
        except etree.LxmlError as exc:  # pragma: no cover - defensive
            raise ValidatorUnavailableError("unreadable validator report") from exc
