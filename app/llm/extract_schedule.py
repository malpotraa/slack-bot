"""Structured intent extraction for /wrike scheduling.

Single LLM call, JSON schema response. We deliberately use Haiku (fast + cheap)
because this is structured extraction, not reasoning.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from anthropic import AsyncAnthropic
from loguru import logger

from app.agent.prompts import SLOT_EXTRACTION_SYSTEM_PROMPT
from app.config import settings
from app.utils.timezone import now_in

_client_singleton: AsyncAnthropic | None = None


def _client() -> AsyncAnthropic:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client_singleton


@dataclass
class ScheduleIntent:
    task_index: int | None
    date_iso: str | None
    start_time: str | None
    duration_minutes: int | None
    force_overlap: bool


async def extract_schedule_intent(
    *,
    user_message: str,
    candidate_tasks: list[dict],
    tz_name: str,
) -> ScheduleIntent:
    today_iso = now_in(tz_name).date().isoformat()
    # Use .replace() instead of .format() so literal {} in the prompt's example
    # JSON output isn't treated as format placeholders.
    system = SLOT_EXTRACTION_SYSTEM_PROMPT.replace("{today_iso}", today_iso).replace(
        "{tz}", tz_name
    )

    candidate_block = "\n".join(
        f"{i+1}. {t.get('title') or '(untitled)'}" for i, t in enumerate(candidate_tasks)
    )
    user_content = (
        f"CANDIDATE TASKS:\n{candidate_block}\n\nUSER MESSAGE:\n{user_message.strip()}\n\n"
        f"Return ONLY the JSON object. No prose."
    )

    resp = await _client().messages.create(
        model=settings.extraction_model,
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )

    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    # Strip code fences if present
    if text.startswith("```"):
        text = text.strip("`")
        # remove leading "json\n" if any
        if text.lower().startswith("json"):
            text = text.split("\n", 1)[-1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning(f"extract JSON parse failed: {exc} | text={text!r}")
        return ScheduleIntent(None, None, None, None, False)

    return ScheduleIntent(
        task_index=_int_or_none(data.get("task_index")),
        date_iso=_str_or_none(data.get("date_iso")),
        start_time=_str_or_none(data.get("start_time")),
        duration_minutes=_int_or_none(data.get("duration_minutes")),
        force_overlap=bool(data.get("force_overlap", False)),
    )


def _int_or_none(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _str_or_none(v) -> str | None:
    if v is None or v == "":
        return None
    return str(v)
