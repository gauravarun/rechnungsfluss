"""LLM call tracing to Langfuse Cloud (EU) (ADR-0011).

- ``TracingCall`` wraps any ``LlmCall``; replayed cassettes are not traced
  because they are wrapped only in live mode.
- Without ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` tracing is a no-op,
  so local runs and CI need no account.
- The PDF is **never** sent: a trace carries its SHA-256 and size, the prompt
  version, model, effort, token usage, latency and the structured output.
- Identifiers in the output (IBAN, VAT IDs, e-mail addresses, phone numbers)
  are masked by default. The data is synthetic, but the habit has to be in
  place before any real document is processed (docs/security.md, W6).
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from typing import Any, Protocol

from rechnungsfluss.extraction.claude import LlmCall, LlmRequest, LlmResponse

_MASKED_KEYS = re.compile(r"(iban|vat_id|email|phone)$")


def mask(value: Any) -> Any:
    """Replace identifier values in nested output with '***'."""
    if isinstance(value, Mapping):
        return {
            k: ("***" if _MASKED_KEYS.search(str(k)) and v else mask(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [mask(v) for v in value]
    return value


class Tracer(Protocol):
    def record(
        self, request: LlmRequest, response: LlmResponse | None, error: BaseException | None
    ) -> None: ...

    def flush(self) -> None: ...


class NoopTracer:
    def record(
        self, request: LlmRequest, response: LlmResponse | None, error: BaseException | None
    ) -> None:
        return None

    def flush(self) -> None:
        return None


class LangfuseTracer:
    def __init__(self, client: Any, *, mask_output: bool = True) -> None:
        self._client = client
        self._mask = mask_output

    @classmethod
    def from_env(cls) -> LangfuseTracer:
        from langfuse import Langfuse

        return cls(
            Langfuse(
                public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
                secret_key=os.environ["LANGFUSE_SECRET_KEY"],
                host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
                environment=os.environ.get("RF_ENVIRONMENT", "local"),
            )
        )

    def record(
        self, request: LlmRequest, response: LlmResponse | None, error: BaseException | None
    ) -> None:
        metadata = {
            "pdf_sha256": hashlib.sha256(request.pdf).hexdigest(),
            "pdf_bytes": len(request.pdf),
            "prompt_version": request.prompt_version,
            "effort": request.effort,
            **dict(request.metadata),
        }
        with self._client.start_as_current_observation(
            name="extract-invoice",
            as_type="generation",
            model=request.model,
            version=request.prompt_version,
            input={"document": "<pdf omitted>", **metadata},
            metadata=metadata,
            model_parameters={"effort": request.effort},
        ) as generation:
            if response is not None:
                output = mask(response.output) if self._mask else response.output
                generation.update(
                    output=output,
                    usage_details={
                        "input": int(response.usage.get("input_tokens", 0)),
                        "output": int(response.usage.get("output_tokens", 0)),
                    },
                    metadata={
                        **metadata,
                        "latency_ms": response.latency_ms,
                        "stop_reason": response.stop_reason,
                        "served_by": response.model,
                    },
                )
            if error is not None:
                generation.update(level="ERROR", status_message=f"{type(error).__name__}: {error}")

    def flush(self) -> None:
        self._client.flush()


def tracer_from_env() -> Tracer:
    if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
        return LangfuseTracer.from_env()
    return NoopTracer()


class TracingCall:
    """Wraps an LlmCall; every call (success or failure) becomes one generation."""

    def __init__(self, inner: LlmCall, tracer: Tracer) -> None:
        self._inner, self._tracer = inner, tracer

    def __call__(self, request: LlmRequest) -> LlmResponse:
        try:
            response = self._inner(request)
        except Exception as exc:
            self._tracer.record(request, None, exc)
            raise
        self._tracer.record(request, response, None)
        return response
