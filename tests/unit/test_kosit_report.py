"""Report parsing against minimal VARL documents shaped like real KoSIT 1.6.3 output."""

import pytest

from rechnungsfluss.validation.kosit import KositValidator, Severity, parse_report

_HEAD = (
    b'<rep:report xmlns:rep="http://www.xoev.de/de/validator/varl/1" '
    b'xmlns:s="http://www.xoev.de/de/validator/framework/1/scenarios" valid="true">'
)


def _report(body: bytes) -> bytes:
    return _HEAD + body + b"</rep:report>"


def test_accepted_report() -> None:
    result = parse_report(
        _report(
            b"<rep:scenarioMatched><s:scenario><s:name>EN16931 (CII)</s:name></s:scenario>"
            b"</rep:scenarioMatched><rep:assessment><rep:accept/></rep:assessment>"
        )
    )
    assert result.accepted
    assert result.scenario == "EN16931 (CII)"
    assert result.findings == ()


def test_rejected_report_collects_rule_codes() -> None:
    result = parse_report(
        _report(
            b"<rep:scenarioMatched><s:scenario><s:name>EN16931 XRechnung (CII)</s:name>"
            b"</s:scenario>"
            b'<rep:validationStepResult valid="false">'
            b'<rep:message id="1" level="error" code="BR-CO-15" xpathLocation="/x">'
            b"  Invoice total amount with VAT ... </rep:message>"
            b'<rep:message id="2" level="information" code="BR-DE-TMP-32">note</rep:message>'
            b"</rep:validationStepResult></rep:scenarioMatched>"
            b"<rep:assessment><rep:reject/></rep:assessment>"
        )
    )
    assert not result.accepted
    assert result.error_codes == {"BR-CO-15"}
    assert result.findings[0].message == "Invoice total amount with VAT ..."
    assert result.findings[1].severity is Severity.INFORMATION


def test_no_scenario_matched_is_rejected() -> None:
    result = parse_report(
        _report(b"<rep:noScenarioMatched/><rep:assessment><rep:reject/></rep:assessment>")
    )
    assert not result.accepted
    assert result.scenario is None


def test_unknown_level_is_treated_as_error() -> None:
    result = parse_report(
        _report(b'<rep:message level="fatal" code="X">x</rep:message><rep:assessment/>')
    )
    assert result.error_codes == {"X"}


@pytest.mark.parametrize("url", ["http://evil.example.com", "ftp://127.0.0.1", "file:///etc"])
def test_validator_refuses_plain_http_to_remote_hosts(url: str) -> None:
    with pytest.raises(ValueError, match="KoSIT URL"):
        KositValidator(url)
