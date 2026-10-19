"""LLM tracing: masking, wrapper behaviour, and what the real Langfuse client would export."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from rechnungsfluss.extraction.claude import LlmRequest, LlmResponse
from rechnungsfluss.observability.tracing import (
    LangfuseTracer,
    NoopTracer,
    TracingCall,
    mask,
    tracer_from_env,
)
from tests.unit.test_extraction import extracted

PDF = b"%PDF-1.7 synthetic test document with a recognisable marker XYZZY"


def _request() -> LlmRequest:
    return LlmRequest(
        pdf=PDF,
        prompt_version="v1",
        model="anthropic.claude-opus-5-5",
        effort="medium",
        metadata=(("tenant", "nordwerk-de"),),
    )


def _response() -> LlmResponse:
    return LlmResponse(
        output=extracted(),
        model="anthropic.claude-opus-5-5",
        stop_reason="end_turn",
        usage={"input_tokens": 2500, "output_tokens": 800},
        latency_ms=3100,
    )


def test_mask_hides_identifiers_but_keeps_amounts() -> None:
    masked = mask(extracted())
    assert masked["seller_iban"] == "***"
    assert masked["seller"]["vat_id"] == "***"
    assert masked["seller"]["email"] == "***"
    assert masked["seller_contact"]["phone"] == "***"
    assert masked["buyer"]["vat_id"] == "***"
    assert masked["totals"]["amount_due"] == "148.75"
    assert masked["lines"][0]["net_price"] == "12.50"


def test_mask_leaves_missing_values_alone() -> None:
    assert mask({"seller_iban": None, "email": ""}) == {"seller_iban": None, "email": ""}


class RecordingTracer:
    def __init__(self) -> None:
        self.calls: list[tuple[LlmRequest, LlmResponse | None, BaseException | None]] = []

    def record(
        self, request: LlmRequest, response: LlmResponse | None, error: BaseException | None
    ) -> None:
        self.calls.append((request, response, error))

    def flush(self) -> None:
        return None


def test_tracing_call_records_success_and_failure() -> None:
    tracer = RecordingTracer()
    assert TracingCall(lambda r: _response(), tracer)(_request()) == _response()

    def boom(request: LlmRequest) -> LlmResponse:
        raise TimeoutError("bedrock timed out")

    with pytest.raises(TimeoutError):
        TracingCall(boom, tracer)(_request())
    assert [(r is not None, e is not None) for _, r, e in tracer.calls] == [
        (True, False),
        (False, True),
    ]


def test_no_keys_means_no_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert isinstance(tracer_from_env(), NoopTracer)


def test_langfuse_export_contains_usage_but_no_document_or_identifiers() -> None:
    from langfuse import Langfuse
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    client = Langfuse(
        public_key="pk-lf-test",
        secret_key="sk-lf-test",  # noqa: S106 - fake keys
        host="https://cloud.langfuse.com",
        span_exporter=exporter,
    )
    tracer = LangfuseTracer(client)
    tracer.record(_request(), _response(), None)
    tracer.flush()
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    exported: Any = " ".join(str(v) for v in (spans[0].attributes or {}).values())
    assert "anthropic.claude-opus-5-5" in exported
    assert "2500" in exported  # input tokens
    assert "nordwerk-de" in exported  # tenant as trace metadata
    assert "XYZZY" not in exported  # no document content...
    assert base64.b64encode(PDF).decode()[:20] not in exported  # ...not even base64
    assert "DE89370400440532013000" not in exported  # IBAN masked
    assert "DE999999999" not in exported  # VAT ID masked
    client.shutdown()
