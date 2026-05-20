"""Block Kit pieces used by /wrike."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.utils.slack_mrkdwn import escape_slack_text
from app.utils.timezone import user_tz
from app.utils.working_hours import TimeSlot


def _fmt_time(dt: datetime, tz_name: str) -> str:
    return dt.astimezone(user_tz(tz_name)).strftime("%-I:%M%p").lower()


def _fmt_day(dt: datetime, tz_name: str) -> str:
    return dt.astimezone(user_tz(tz_name)).strftime("%a %b %-d")


def _hms(start: datetime, end: datetime) -> str:
    mins = int((end - start).total_seconds() // 60)
    h, m = divmod(mins, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def divider() -> dict:
    return {"type": "divider"}


# Slack section.text.text has a 3000-char hard limit. Stay safely under.
_MAX_SECTION_CHARS = 2900


def _section_chunks(text: str) -> list[dict]:
    """Split text on line boundaries into one-or-more section blocks ≤ 2900 chars."""
    if len(text) <= _MAX_SECTION_CHARS:
        return [section(text)]
    out: list[dict] = []
    current: list[str] = []
    current_len = 0
    for line in text.split("\n"):
        if current_len + len(line) + 1 > _MAX_SECTION_CHARS and current:
            out.append(section("\n".join(current)))
            current = [line]
            current_len = len(line) + 1
        else:
            current.append(line)
            current_len += len(line) + 1
    if current:
        out.append(section("\n".join(current)))
    return out


def task_list_blocks(tasks: list[dict]) -> list[dict]:
    if not tasks:
        return [section("📋  No Wrike tasks in 'New' status assigned to you. 🎉")]
    lines = [f"📋  *{len(tasks)} new Wrike tasks assigned to you:*"]
    for i, t in enumerate(tasks, start=1):
        title = t.get("title") or "(untitled)"
        perma = t.get("permalink") or ""
        due = (t.get("due_date") or "")
        due_str = f" — _due {due}_" if due else ""
        safe_title = escape_slack_text(title)
        link = f"<{perma}|{safe_title}>" if perma else safe_title
        lines.append(f"  *{i}.*  {link}{due_str}")
    return _section_chunks("\n".join(lines))


def slots_block(slots_by_day: list[tuple[datetime, list[TimeSlot]]], tz_name: str) -> list[dict]:
    if not slots_by_day:
        return []
    lines = ["📅  *Open slots in your calendar:*"]
    for day, slots in slots_by_day[:3]:
        lines.append(f"\n*{_fmt_day(day, tz_name)}*")
        for s in slots[:4]:
            lines.append(
                f"  • {_fmt_time(s.start, tz_name)}–{_fmt_time(s.end, tz_name)}  "
                f"({_hms(s.start, s.end)})"
            )
    return _section_chunks("\n".join(lines))


def prompt_block() -> list[dict]:
    return [
        section(
            "Reply with what to schedule, e.g. _“schedule #2 tomorrow 9–11”_ "
            "or _“#1 Friday 2pm for 90 min”_."
        )
    ]


def overlap_warning(
    proposed_start: datetime,
    proposed_end: datetime,
    overlapping: list[dict[str, Any]],
    suggested: TimeSlot | None,
    tz_name: str,
) -> list[dict]:
    overlap_lines = "\n".join(
        f"  • {escape_slack_text(ov.get('title') or '(untitled)')} "
        f"({_fmt_time(_p(ov['start']), tz_name)}–{_fmt_time(_p(ov['end']), tz_name)})"
        for ov in overlapping[:3]
    )
    blocks = [
        section(
            f"⚠️  *{_fmt_time(proposed_start, tz_name)}–{_fmt_time(proposed_end, tz_name)} "
            f"on {_fmt_day(proposed_start, tz_name)} overlaps with:*\n{overlap_lines}"
        )
    ]
    if suggested:
        blocks.append(
            section(
                f"_Next free slot of the same length:_  "
                f"*{_fmt_day(suggested.start, tz_name)} "
                f"{_fmt_time(suggested.start, tz_name)}–"
                f"{_fmt_time(suggested.end, tz_name)}*"
            )
        )
    blocks.append(
        section(
            "Reply *“use suggested”*, *“schedule anyway”*, or give me a different time."
        )
    )
    return blocks


def created_block(*, title: str, start: datetime, end: datetime, tz_name: str, link: str) -> list[dict]:
    return [
        section(
            f"✅  *Created:* {escape_slack_text(title)}\n"
            f"_{_fmt_day(start, tz_name)}, {_fmt_time(start, tz_name)}–"
            f"{_fmt_time(end, tz_name)}_\n"
            f"<{link}|Wrike task>"
        )
    ]


def _p(s: str | datetime) -> datetime:
    if isinstance(s, datetime):
        return s
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
