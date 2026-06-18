"""Braintrust / OpenTelemetry setup. Call configure_observability() once at startup.

Traces are exported to Braintrust via the standard OTLP HTTP exporter so the
existing span hierarchy (agent.turn → agent.iteration → tool.*) is preserved.
Set BRAINTRUST_API_KEY to enable; leave empty to run with a no-op tracer.

Redaction policy (enforced in runner._trace_tool_output):
  - User prompts, tool names/args, and assistant replies → traced in full.
  - Tool RESPONSES (Google Ads / Calendar / Wrike data) → shape + types only
    (values masked via redact_values) unless TRACE_SENSITIVE_DATA=true.
"""

from __future__ import annotations

from typing import Any, ContextManager

from loguru import logger
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry.trace import Span

from app.config import settings

_tracer = None


def configure_observability() -> None:
    """Register Braintrust OTEL tracer + optionally auto-instrument Anthropic SDK."""
    global _tracer

    if not settings.braintrust_api_key:
        logger.info("BRAINTRUST_API_KEY empty; tracing disabled")
        return

    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("opentelemetry packages not installed; tracing disabled")
        return

    exporter = OTLPSpanExporter(
        endpoint="https://api.braintrust.dev/otel/v1/traces",
        headers={
            "Authorization": f"Bearer {settings.braintrust_api_key}",
            # Routes traces to the correct Braintrust project.
            "x-bt-project": settings.braintrust_project,
        },
    )
    resource = Resource(attributes={"service.name": "slack-assistant"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    otel_trace.set_tracer_provider(provider)

    if settings.trace_sensitive_data:
        try:
            from openinference.instrumentation.anthropic import AnthropicInstrumentor

            AnthropicInstrumentor().instrument(tracer_provider=provider)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"Anthropic instrumentation skipped: {exc}")
    else:
        logger.info("Anthropic auto-instrumentation skipped; TRACE_SENSITIVE_DATA=false")

    _tracer = provider.get_tracer("slack-assistant")
    logger.info(f"Braintrust tracer registered (project={settings.braintrust_project})")


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
    renders correctly in Braintrust (Agent / Chain / Tool / LLM …
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


def redact_values(obj: Any) -> Any:
    """Return a same-shaped copy of `obj` with every leaf value replaced by a
    type token (`<str:11>`, `<int>`, `<float>`, `<bool>`).

    Dict keys and list structure are preserved, so a trace shows the *shape*
    and types of a tool response — useful for observability and evals — without
    exposing the actual values (spend, campaign names, event titles, etc.).
    Lists collapse to the first element's shape plus a count.
    """
    if obj is None:
        return None
    # bool is a subclass of int — check it first.
    if isinstance(obj, bool):
        return "<bool>"
    if isinstance(obj, int):
        return "<int>"
    if isinstance(obj, float):
        return "<float>"
    if isinstance(obj, str):
        return f"<str:{len(obj)}>"
    if isinstance(obj, dict):
        return {str(k): redact_values(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        if not obj:
            return []
        head = redact_values(obj[0])
        if len(obj) == 1:
            return [head]
        return [head, f"<+{len(obj) - 1} more items>"]
    return f"<{type(obj).__name__}>"


# ── Cost estimation ────────────────────────────────────────────────────────

# Per-million-token USD rates. `cache_write` uses the 1-hour-cache rate (2x the
# input rate) since the agent caches its static prefix with a 1h TTL.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.30,
        "cache_write": 6.0,
    },
    "claude-haiku-4-5": {
        "input": 1.0,
        "output": 5.0,
        "cache_read": 0.10,
        "cache_write": 2.0,
    },
}


def estimate_cost_usd(
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Estimate the USD cost of one model turn from its token usage.

    Returns 0.0 for an unrecognised model. `input_tokens` is the *uncached*
    input — Anthropic reports cache reads/writes as separate counts.
    """
    rates: dict[str, float] | None = None
    for prefix, table in MODEL_PRICING.items():
        if model.startswith(prefix):
            rates = table
            break
    if rates is None:
        return 0.0
    total = (
        input_tokens * rates["input"]
        + output_tokens * rates["output"]
        + cache_read_tokens * rates["cache_read"]
        + cache_write_tokens * rates["cache_write"]
    )
    return round(total / 1_000_000, 6)
