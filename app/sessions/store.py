"""Per-thread session store backed by SQLite (ConversationSession table)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.db.engine import session_scope
from app.db.models import ConversationSession


async def _find(
    session: AsyncSession, *, user_id: int, channel_id: str, thread_ts: str
) -> ConversationSession | None:
    stmt = select(ConversationSession).where(
        ConversationSession.user_id == user_id,
        ConversationSession.channel_id == channel_id,
        ConversationSession.thread_ts == thread_ts,
    )
    return (await session.exec(stmt)).first()


async def get_command_state(
    *, user_id: int, channel_id: str, thread_ts: str, command_name: str
) -> dict | None:
    """Return command state if there's a non-expired matching session."""
    async with session_scope() as session:
        row = await _find(session, user_id=user_id, channel_id=channel_id, thread_ts=thread_ts)
        if row is None or row.command_name != command_name:
            return None
        if row.expires_at and row.expires_at < datetime.now(UTC):
            return None
        if not row.command_state_json:
            return None
        return json.loads(row.command_state_json)


async def save_command_state(
    *,
    user_id: int,
    channel_id: str,
    thread_ts: str,
    command_name: str,
    state: dict,
    ttl_minutes: int = 30,
) -> None:
    expires = datetime.now(UTC) + timedelta(minutes=ttl_minutes)
    async with session_scope() as session:
        row = await _find(session, user_id=user_id, channel_id=channel_id, thread_ts=thread_ts)
        if row is None:
            row = ConversationSession(
                user_id=user_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                command_name=command_name,
                command_state_json=json.dumps(state, default=str),
                expires_at=expires,
            )
        else:
            row.command_name = command_name
            row.command_state_json = json.dumps(state, default=str)
            row.expires_at = expires
            row.updated_at = datetime.now(UTC)
        session.add(row)


async def clear_command_state(
    *, user_id: int, channel_id: str, thread_ts: str
) -> None:
    async with session_scope() as session:
        row = await _find(session, user_id=user_id, channel_id=channel_id, thread_ts=thread_ts)
        if row is not None:
            row.command_name = None
            row.command_state_json = None
            row.expires_at = None
            row.updated_at = datetime.now(UTC)
            session.add(row)


# ── Conversational agent history ───────────────────────────────────────────


async def load_agent_history(
    *, user_id: int, channel_id: str, thread_ts: str
) -> list[dict[str, Any]]:
    async with session_scope() as session:
        row = await _find(session, user_id=user_id, channel_id=channel_id, thread_ts=thread_ts)
        if row is None or not row.agent_history_json:
            return []
        return json.loads(row.agent_history_json)


async def append_agent_turn(
    *,
    user_id: int,
    channel_id: str,
    thread_ts: str,
    user_msg: str,
    assistant_msg: str,
    keep_recent_turns: int = 6,
    summarize_when_more_than: int = 12,
) -> None:
    """Append (user, assistant) and roll older history into a summary if it gets long."""
    async with session_scope() as session:
        row = await _find(session, user_id=user_id, channel_id=channel_id, thread_ts=thread_ts)
        history: list[dict] = []
        if row is not None and row.agent_history_json:
            history = json.loads(row.agent_history_json)
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": assistant_msg})

        # Compaction: if we have many messages, replace the older portion with
        # a single rolling-summary system note. Recent turns stay verbatim.
        if len(history) > summarize_when_more_than * 2:
            keep_n = keep_recent_turns * 2
            older = history[:-keep_n]
            recent = history[-keep_n:]
            try:
                summary_text = await _summarize_history(older)
            except Exception:  # pragma: no cover
                summary_text = None
            if summary_text:
                history = [
                    {"role": "user", "content": f"[Earlier conversation summary]\n{summary_text}"},
                    {
                        "role": "assistant",
                        "content": "Got it — I'll keep that context in mind.",
                    },
                    *recent,
                ]
            else:
                history = recent

        if row is None:
            row = ConversationSession(
                user_id=user_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                agent_history_json=json.dumps(history),
            )
        else:
            row.agent_history_json = json.dumps(history)
            row.updated_at = datetime.now(UTC)
        session.add(row)


async def append_settled_action(
    *,
    user_id: int,
    channel_id: str,
    thread_ts: str,
    outcome: str,
    summary: str,
) -> None:
    """Record an approval-card outcome into the conversation history so the
    NEXT agent turn sees it (e.g. user says "do that for next week too" right
    after clicking Approve).

    outcome: "approved" / "approved_alternate" / "cancelled"
    summary: short human-readable description of what settled (the same text
        used in the card body).
    """
    async with session_scope() as session:
        row = await _find(
            session, user_id=user_id, channel_id=channel_id, thread_ts=thread_ts
        )
        history: list[dict] = []
        if row is not None and row.agent_history_json:
            history = json.loads(row.agent_history_json)
        # Inject a synthetic user+assistant exchange so the model sees it on
        # the next turn through the normal history channel. Kept short to
        # avoid bloating the cache miss.
        history.append(
            {
                "role": "user",
                "content": f"[system note: approval card settled — {outcome}]",
            }
        )
        history.append({"role": "assistant", "content": f"Logged: {summary}"})

        if row is None:
            row = ConversationSession(
                user_id=user_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                agent_history_json=json.dumps(history),
            )
        else:
            row.agent_history_json = json.dumps(history)
            row.updated_at = datetime.now(UTC)
        session.add(row)


async def _summarize_history(history: list[dict[str, Any]]) -> str | None:
    """Compress old turns into a short summary via Haiku. Returns None on failure."""
    # Lazy imports to avoid pulling Anthropic on every session-store call.
    try:
        from anthropic import AsyncAnthropic

        from app.agent.prompts import HISTORY_SUMMARY_SYSTEM_PROMPT
        from app.config import settings as _settings

        client = AsyncAnthropic(api_key=_settings.anthropic_api_key)
    except Exception:
        return None

    # Render history into a plain text block; tool_use/tool_result entries are
    # complex objects so we stringify them.
    lines: list[str] = []
    for msg in history:
        role = msg.get("role", "?")
        content = msg.get("content")
        if isinstance(content, str):
            lines.append(f"{role.upper()}: {content}")
        else:
            lines.append(f"{role.upper()}: {json.dumps(content, default=str)[:1000]}")
    flat = "\n\n".join(lines)
    if len(flat) > 12000:
        flat = flat[-12000:]

    try:
        resp = await client.messages.create(
            model=_settings.extraction_model,
            max_tokens=400,
            system=HISTORY_SUMMARY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": flat}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return text or None
    except Exception:
        return None
