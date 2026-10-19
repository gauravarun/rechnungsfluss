"""PDF extraction with Claude on Amazon Bedrock (ADR-0004).

- Structured outputs (``output_format``): the response is guaranteed to match
  ``ExtractedInvoice``. Forced tool use is not available on Claude Opus 5.5.
- Calls go through an ``LlmCall`` so the same extractor runs live, records
  cassettes, or replays them in CI without credentials (ADR-0010).
- Refusals are handled with the SDK's client-side fallback middleware (server-
  side fallbacks are not offered on Bedrock); a refusal that survives the
  fallback raises ``ExtractionRefusedError`` and the document goes to FAILED.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Literal, Protocol

from rechnungsfluss.domain.tenancy import TenantId
from rechnungsfluss.extraction.mapping import to_canonical
from rechnungsfluss.extraction.schema import ExtractedInvoice
from rechnungsfluss.ports import Extraction, ExtractionError

DEFAULT_MODEL = "anthropic.claude-opus-5-5"
FALLBACK_MODEL = "anthropic.claude-opus-4-8"
PROMPT_VERSION = "v1"
Effort = Literal["low", "medium", "high", "xhigh", "max"]
DEFAULT_EFFORT: Effort = "medium"
MAX_TOKENS = 16000


def load_prompt(version: str = PROMPT_VERSION) -> str:
    return (resources.files("rechnungsfluss.extraction") / "prompts" / f"{version}.md").read_text(
        encoding="utf-8"
    )


@dataclass(frozen=True, slots=True)
class LlmRequest:
    pdf: bytes
    prompt_version: str
    model: str
    effort: Effort
    metadata: tuple[tuple[str, str], ...] = ()
    """Trace context (tenant, eval run). Not part of the cassette key: it doesn't
    change what the model sees."""

    @property
    def key(self) -> str:
        """Cassette key: changes whenever input, prompt, model or effort change."""
        digest = hashlib.sha256(self.pdf).hexdigest()
        return f"{digest[:32]}-{self.prompt_version}-{self.model}-{self.effort}"


@dataclass(frozen=True, slots=True)
class LlmResponse:
    output: dict[str, Any]
    """The structured output (JSON matching ExtractedInvoice)."""
    model: str
    stop_reason: str
    usage: dict[str, int]
    latency_ms: int


class LlmCall(Protocol):
    def __call__(self, request: LlmRequest) -> LlmResponse: ...


class ExtractionRefusedError(ExtractionError):
    """The model (and its fallback) declined; the invoice goes to FAILED, never to approval."""


class CassetteMissingError(LookupError):
    """No recording for this request; run the live eval to (re)record."""


class BedrockCall:
    """Live call through the Anthropic SDK's Bedrock (Mantle) client."""

    def __init__(
        self,
        *,
        region: str,
        profile: str | None = None,
        fallback_model: str | None = FALLBACK_MODEL,
    ) -> None:
        from anthropic import AnthropicBedrockMantle, BetaRefusalFallbackMiddleware

        middleware = (
            [BetaRefusalFallbackMiddleware([{"model": fallback_model}])] if fallback_model else None
        )
        self._client = AnthropicBedrockMantle(
            aws_region=region, aws_profile=profile, middleware=middleware
        )
        self._system = {v: load_prompt(v) for v in (PROMPT_VERSION,)}

    def __call__(self, request: LlmRequest) -> LlmResponse:
        import time

        started = time.monotonic()
        response = self._client.beta.messages.parse(
            model=request.model,
            max_tokens=MAX_TOKENS,
            system=self._system.get(request.prompt_version) or load_prompt(request.prompt_version),
            output_config={"effort": request.effort},
            output_format=ExtractedInvoice,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": base64.standard_b64encode(request.pdf).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": "Extract this invoice."},
                    ],
                }
            ],
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise ExtractionRefusedError(
                f"no structured output (stop_reason={response.stop_reason})"
            )
        usage = response.usage
        return LlmResponse(
            output=response.parsed_output.model_dump(mode="json"),
            model=response.model,
            stop_reason=str(response.stop_reason),
            usage={
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_input_tokens": usage.cache_read_input_tokens or 0,
                "cache_creation_input_tokens": usage.cache_creation_input_tokens or 0,
            },
            latency_ms=latency_ms,
        )


class RecordingCall:
    """Wraps a live call and stores every response as a cassette."""

    def __init__(self, inner: LlmCall, directory: Path) -> None:
        self._inner, self._dir = inner, directory

    def __call__(self, request: LlmRequest) -> LlmResponse:
        response = self._inner(request)
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / f"{request.key}.json").write_text(
            json.dumps(
                {
                    "output": response.output,
                    "model": response.model,
                    "stop_reason": response.stop_reason,
                    "usage": response.usage,
                    "latency_ms": response.latency_ms,
                },
                indent=1,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return response


class ReplayCall:
    """Replays recorded responses; never touches the network (PR CI, ADR-0010)."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def __call__(self, request: LlmRequest) -> LlmResponse:
        path = self._dir / f"{request.key}.json"
        if not path.is_file():
            raise CassetteMissingError(request.key)
        data = json.loads(path.read_text("utf-8"))
        return LlmResponse(
            output=data["output"],
            model=data["model"],
            stop_reason=data["stop_reason"],
            usage=data["usage"],
            latency_ms=data["latency_ms"],
        )


class ClaudeExtractor:
    def __init__(
        self,
        call: LlmCall,
        *,
        model: str = DEFAULT_MODEL,
        prompt_version: str = PROMPT_VERSION,
        effort: Effort = DEFAULT_EFFORT,
    ) -> None:
        self._call = call
        self.model, self.prompt_version, self.effort = model, prompt_version, effort
        self.trace_metadata: tuple[tuple[str, str], ...] = ()

    def extract(self, tenant: TenantId, pdf: bytes) -> Extraction:
        response = self._call(
            LlmRequest(
                pdf=pdf,
                prompt_version=self.prompt_version,
                model=self.model,
                effort=self.effort,
                metadata=(("tenant", tenant.value), *self.trace_metadata),
            )
        )
        extracted = ExtractedInvoice.model_validate(response.output)
        mapped = to_canonical(extracted)
        return Extraction(
            invoice=mapped.invoice if extracted.is_invoice else None,
            is_invoice=extracted.is_invoice,
            missing_fields=tuple(mapped.missing),
            uncertain_fields=tuple(mapped.uncertain),
            model=response.model,
            prompt_version=self.prompt_version,
            usage=response.usage,
            latency_ms=response.latency_ms,
        )
