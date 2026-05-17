"""Phoenix / OpenTelemetry setup. Call configure_observability() once at startup.

Works against either:
  - Phoenix Cloud:  PHOENIX_COLLECTOR_ENDPOINT=https://app.phoenix.arize.com[/s/<space>]
                    PHOENIX_API_KEY=<jwt>
  - Self-host:      PHOENIX_COLLECTOR_ENDPOINT=http://phoenix:6006   (no API key)

We rely on `phoenix.otel.register()` to wire OTLP correctly: pass the API key via
its `api_key=` parameter (it adds the `authorization: Bearer …` header itself),
pass the bare base URL as `endpoint=`, and let it append `/v1/traces`.
"""

from __future__ import annotations

from typing import Any, ContextManager

from loguru import logger
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry.trace import Span

from app.config import settings

_tracer = None


def _phoenix_endpoint(raw: str) -> str:
    """Return a base URL ending with `/v1/traces` for the OTLP HTTP exporter."""
    base = raw.rstrip("/")
    if base.endswith("/v1/traces"):
        return base
    return f"{base}/v1/traces"


def configure_observability() -> None:
    """Register Phoenix tracer + auto-instrument Anthropic SDK."""
    global _tracer

    if not settings.phoenix_collector_endpoint:
        logger.info("PHOENIX_COLLECTOR_ENDPOINT empty; tracing disabled")
        return

    try:
        from phoenix.otel import register
    except ImportError:
        logger.warning("arize-phoenix-otel not installed; tracing disabled")
        return

    endpoint = _phoenix_endpoint(settings.phoenix_collector_endpoint)
    api_key = settings.phoenix_api_key or None

    tracer_provider = register(
        project_name=settings.phoenix_project_name,
        endpoint=endpoint,
        api_key=api_key,
        auto_instrument=False,
        set_global_tracer_provider=True,
        protocol="http/protobuf",
        verbose=False,
    )

    try:
        from openinference.instrumentation.anthropic import AnthropicInstrumentor

        AnthropicInstrumentor().instrument(tracer_provider=tracer_provider)
    except Exception as exc:  # pragma: no cover
        logger.warning(f"Anthropic instrumentation skipped: {exc}")

    _tracer = tracer_provider.get_tracer("slack-assistant")
    auth_label = "with API key" if api_key else "no auth (self-host)"
    logger.info(
        f"Phoenix tracer registered → {endpoint} "
        f"(project={settings.phoenix_project_name}, {auth_label})"
    )


def get_tracer():
    """Get the OTel tracer; falls back to a no-op tracer if not configured."""
    if _tracer is not None:
        return _tracer
    from opentelemetry import trace

    return trace.get_tracer("slack-assistant")


def start_span(
    name: str,
    *,
    kind: OpenInferenceSpanKindValues,
    attributes: dict[str, Any] | None = None,
) -> ContextManager[Span]:
    """Start a current span tagged with the right OpenInference kind so it
    renders correctly in the Phoenix UI (Agent / Chain / Tool / LLM …
    instead of "unknown").

    Wraps `tracer.start_as_current_span` directly — returns whatever the
    OTel API returns (a context manager). NOT decorated with
    `@contextmanager`: doing so would yield the generator and break the
    `with start_span(...) as span:` ergonomic where `span` is the Span.

    Use the OpenInferenceSpanKindValues enum (`Kind.AGENT`, `Kind.CHAIN`,
    `Kind.TOOL`, …); the helper handles the `.value` unwrap.

    Anthropic-SDK spans are NOT routed through here — the
    AnthropicInstrumentor sets `LLM` on them internally.
    """
    merged: dict[str, Any] = {
        SpanAttributes.OPENINFERENCE_SPAN_KIND: kind.value,
    }
    if attributes:
        merged.update(attributes)
    return get_tracer().start_as_current_span(name, attributes=merged)
