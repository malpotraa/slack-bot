"""Conversational agent runner — Anthropic SDK with streaming + prompt caching.

Latency optimizations baked in:
  • Streaming — caller gets text tokens as they arrive (perceived TTFT ~1–2s)
  • Prompt caching — system prompt + tool definitions are marked
    `cache_control: ephemeral` so subsequent turns hit Anthropic's cache
    instead of re-tokenizing 1000+ tokens of prompt.
  • Tight iteration cap — default 3 (was 8). Forces decisive tool use.

Phoenix tracing remains rich: per-turn span, per-tool span, full input/output.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from anthropic import AsyncAnthropic
from loguru import logger
from opentelemetry import trace

from app.agent.prompts import ASSISTANT_SYSTEM_PROMPT
from app.agent.tools import TOOL_SCHEMAS, dispatch_tool
from app.config import settings
from app.observability import get_tracer
from app.utils.timezone import now_in

_anthropic: AsyncAnthropic | None = None


def _client() -> AsyncAnthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _anthropic


def _tools_with_caching() -> list[dict[str, Any]]:
    """Return TOOL_SCHEMAS with cache_control on the last tool.

    Anthropic prompt caching: a cache breakpoint marks everything BEFORE and
    including it as a cached prefix. Putting the breakpoint on the last tool
    caches the entire tools array. The system prompt is cached separately
    (it has its own breakpoint). Subsequent calls hit the cache in ~5ms.
    """
    if not TOOL_SCHEMAS:
        return []
    tools = [dict(t) for t in TOOL_SCHEMAS]
    tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return tools


def _set_session_attrs(
    span,
    *,
    user_id: int,
    user_name: str,
    user_email: str | None,
    slack_user_id: str,
    slack_team_id: str,
    channel_id: str,
    thread_ts: str,
) -> None:
    span.set_attribute("user.id", slack_user_id or str(user_id))
    span.set_attribute("user.name", user_name or "")
    if user_email:
        span.set_attribute("user.email", user_email)
    span.set_attribute("user.slack_id", slack_user_id)
    span.set_attribute("user.db_id", str(user_id))
    span.set_attribute("user.team_id", slack_team_id)
    span.set_attribute("session.id", f"{channel_id}:{thread_ts}")
    span.set_attribute("slack.channel_id", channel_id)
    span.set_attribute("slack.thread_ts", thread_ts)


StreamCallback = Callable[[str], Awaitable[None]]


def _clean_assistant_block(bd: dict[str, Any]) -> dict[str, Any]:
    """Strip SDK-side extra fields before round-tripping into the next request.

    Newer Anthropic SDK versions decorate text blocks with extras like
    `parsed_output`, `citations`, etc. The API rejects those on inbound
    messages with "Extra inputs are not permitted". Keep only the fields
    Anthropic accepts per block type.
    """
    t = bd.get("type")
    if t == "text":
        return {"type": "text", "text": bd.get("text", "")}
    if t == "tool_use":
        return {
            "type": "tool_use",
            "id": bd.get("id"),
            "name": bd.get("name"),
            "input": bd.get("input", {}),
        }
    if t == "thinking":
        # Extended-thinking blocks must be passed back verbatim if you use
        # them; we currently don't, but be safe.
        return {k: v for k, v in bd.items() if k in {"type", "thinking", "signature"}}
    # Unknown — pass through; Anthropic will tell us if it's wrong.
    return bd


async def run_agent_turn(
    *,
    user_id: int,
    user_name: str,
    user_email: str | None = None,
    user_tz: str,
    workday_start: str,
    workday_end: str,
    history: list[dict[str, Any]],
    user_message: str,
    extra_system_context: str | None = None,
    slack_user_id: str = "",
    slack_team_id: str = "",
    slack_bot_token: str | None = None,
    channel_id: str = "",
    thread_ts: str = "",
    max_tool_iterations: int = 3,
    on_stream_chunk: StreamCallback | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Run one user turn through Claude with streaming + prompt caching.

    `on_stream_chunk(accumulated_text)` is invoked on every text delta. The
    caller is responsible for debouncing chat.update calls — we don't here.
    """
    tracer = get_tracer()
    today_iso = now_in(user_tz).date().isoformat()
    system_text = ASSISTANT_SYSTEM_PROMPT.format(
        user_name=user_name,
        tz=user_tz,
        today_iso=today_iso,
        work_start=workday_start,
        work_end=workday_end,
    )
    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    # Per-turn extra context (e.g. "the user is in a /wrike thread, here are
    # the tasks they see"). Not cache-marked because it varies per turn.
    if extra_system_context:
        system_blocks.append({"type": "text", "text": extra_system_context})
    tools = _tools_with_caching()

    messages: list[dict[str, Any]] = list(history) + [
        {"role": "user", "content": user_message}
    ]

    with tracer.start_as_current_span("agent.turn") as span:
        _set_session_attrs(
            span,
            user_id=user_id,
            user_name=user_name,
            user_email=user_email,
            slack_user_id=slack_user_id,
            slack_team_id=slack_team_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )
        span.set_attribute("input.value", user_message)
        span.set_attribute("input.mime_type", "text/plain")
        span.set_attribute("agent.model", settings.agent_model)
        span.set_attribute("agent.history_messages", len(history))

        tools_used: list[str] = []
        # Tools that returned preview=True become pending_actions; the caller
        # builds an Approve / Disapprove card for each.
        pending_actions: list[dict[str, Any]] = []
        total_input_tokens = 0
        total_output_tokens = 0
        total_cache_read_tokens = 0
        total_cache_create_tokens = 0

        for iteration in range(max_tool_iterations):
            with tracer.start_as_current_span("agent.iteration") as it_span:
                it_span.set_attribute("iteration", iteration + 1)

                accumulated_text = ""
                async with _client().messages.stream(
                    model=settings.agent_model,
                    max_tokens=2048,
                    system=system_blocks,
                    tools=tools,
                    messages=messages,
                ) as stream:
                    async for chunk in stream.text_stream:
                        accumulated_text += chunk
                        if on_stream_chunk is not None:
                            try:
                                await on_stream_chunk(accumulated_text)
                            except Exception as exc:  # pragma: no cover
                                logger.warning(f"stream callback failed: {exc}")
                    final = await stream.get_final_message()

                it_span.set_attribute("stop_reason", final.stop_reason or "")
                if final.usage:
                    it_span.set_attribute("llm.token_count.prompt", final.usage.input_tokens)
                    it_span.set_attribute(
                        "llm.token_count.completion", final.usage.output_tokens
                    )
                    cache_read = getattr(final.usage, "cache_read_input_tokens", 0) or 0
                    cache_create = (
                        getattr(final.usage, "cache_creation_input_tokens", 0) or 0
                    )
                    it_span.set_attribute("llm.token_count.cache_read", cache_read)
                    it_span.set_attribute(
                        "llm.token_count.cache_create", cache_create
                    )
                    total_input_tokens += final.usage.input_tokens
                    total_output_tokens += final.usage.output_tokens
                    total_cache_read_tokens += cache_read
                    total_cache_create_tokens += cache_create

            # Process final message blocks. We clean SDK-decorated extras off
            # text/tool_use blocks before appending them to the messages list
            # so the NEXT iteration's request body is accepted by the API.
            assistant_blocks: list[dict[str, Any]] = []
            tool_uses: list[dict[str, Any]] = []
            text_parts: list[str] = []
            for block in final.content:
                bd = block.model_dump()
                cleaned = _clean_assistant_block(bd)
                assistant_blocks.append(cleaned)
                if cleaned.get("type") == "tool_use":
                    tool_uses.append(cleaned)
                elif cleaned.get("type") == "text":
                    text_parts.append(cleaned.get("text", ""))
            messages.append({"role": "assistant", "content": assistant_blocks})

            if final.stop_reason != "tool_use" or not tool_uses:
                final_text = "\n".join(p for p in text_parts if p).strip()
                span.set_attribute("output.value", final_text or "(empty)")
                span.set_attribute("output.mime_type", "text/plain")
                span.set_attribute("agent.iterations", iteration + 1)
                span.set_attribute("agent.tools_used", json.dumps(tools_used))
                span.set_attribute("llm.token_count.prompt_total", total_input_tokens)
                span.set_attribute(
                    "llm.token_count.completion_total", total_output_tokens
                )
                span.set_attribute(
                    "llm.token_count.cache_read_total", total_cache_read_tokens
                )
                span.set_attribute(
                    "llm.token_count.cache_create_total", total_cache_create_tokens
                )
                return (final_text or "(no response)", pending_actions)

            # Tool-use round — execute tools and continue
            tool_results = []
            for tu in tool_uses:
                tool_name = tu["name"]
                tool_input = tu.get("input", {})
                tools_used.append(tool_name)
                with tracer.start_as_current_span(f"tool.{tool_name}") as ts:
                    _set_session_attrs(
                        ts,
                        user_id=user_id,
                        user_name=user_name,
                        user_email=user_email,
                        slack_user_id=slack_user_id,
                        slack_team_id=slack_team_id,
                        channel_id=channel_id,
                        thread_ts=thread_ts,
                    )
                    ts.set_attribute("tool.name", tool_name)
                    ts.set_attribute("tool.input", json.dumps(tool_input)[:8000])
                    ts.set_attribute("input.value", json.dumps(tool_input)[:8000])
                    ts.set_attribute("input.mime_type", "application/json")
                    is_error = False
                    try:
                        result = await dispatch_tool(
                            tool_name,
                            tool_input,
                            user_id=user_id,
                            user_tz=user_tz,
                            workday_start=workday_start,
                            workday_end=workday_end,
                            slack_user_id=slack_user_id,
                            slack_bot_token=slack_bot_token,
                        )
                    except Exception as exc:
                        logger.exception(f"tool {tool_name} failed")
                        result = {"error": str(exc)}
                        ts.set_status(trace.StatusCode.ERROR, str(exc))
                        is_error = True
                    ts.set_attribute("tool.output", json.dumps(result, default=str)[:8000])
                    ts.set_attribute("output.value", json.dumps(result, default=str)[:8000])
                    ts.set_attribute("output.mime_type", "application/json")
                    if isinstance(result, dict) and result.get("preview"):
                        ts.set_attribute("tool.is_preview", True)
                        # Capture for the handler to build an Approve card.
                        # Pass the LLM's original args plus the summary text.
                        pending_actions.append(
                            {
                                "tool": tool_name,
                                "args": tool_input,
                                "summary": result.get("summary_for_user", ""),
                                "resolved_task_id": result.get("_resolved_task_id"),
                                "alternate": result.get("alternate"),
                            }
                        )

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": json.dumps(result, default=str),
                        "is_error": is_error,
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        # Hit the iteration cap
        span.set_attribute("agent.iterations", max_tool_iterations)
        span.set_attribute("output.value", "(tool-iteration limit reached)")
        return (
            "I hit my tool-use limit before finishing — try asking again more specifically.",
            pending_actions,
        )
