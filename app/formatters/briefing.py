"""Block Kit formatter for /goodmorning."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.integrations.slack_search import UnrepliedMention
from app.utils.timezone import user_tz
from app.utils.working_hours import TimeSlot


def _fmt_time(dt: datetime, tz_name: str) -> str:
    return dt.astimezone(user_tz(tz_name)).strftime("%-I:%M%p").lower()


def _fmt_day(dt: datetime, tz_name: str) -> str:
    local = dt.astimezone(user_tz(tz_name))
    return local.strftime("%a %b %-d")


def _bucket_mentions(
    mentions: list[UnrepliedMention], today: datetime, tz_name: str
) -> tuple[list[UnrepliedMention], list[UnrepliedMention]]:
    z = user_tz(tz_name)
    yesterday_start = (today.astimezone(z) - timedelta(days=1)).date()
    yesterday: list[UnrepliedMention] = []
    earlier: list[UnrepliedMention] = []
    for m in mentions:
        d = m.posted_at.astimezone(z).date()
        if d == yesterday_start or d == today.astimezone(z).date():
            yesterday.append(m)
        else:
            earlier.append(m)
    return yesterday, earlier


def _slack_section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


# Slack section.text.text has a 3000-char hard limit. Stay safely under.
_MAX_SECTION_CHARS = 2900


def _section_chunks(text: str) -> list[dict]:
    """Split text on line boundaries into one-or-more section blocks ≤ 2900 chars."""
    if len(text) <= _MAX_SECTION_CHARS:
        return [_slack_section(text)]
    out: list[dict] = []
    current: list[str] = []
    current_len = 0
    for line in text.split("\n"):
        if current_len + len(line) + 1 > _MAX_SECTION_CHARS and current:
            out.append(_slack_section("\n".join(current)))
            current = [line]
            current_len = len(line) + 1
        else:
            current.append(line)
            current_len += len(line) + 1
    if current:
        out.append(_slack_section("\n".join(current)))
    return out


def _channel_label(m: UnrepliedMention) -> str:
    if m.is_dm:
        return "in DM"
    return f"in #{m.channel_name}" if m.channel_name else ""


def _mention_line(m: UnrepliedMention, tz_name: str) -> str:
    when = _fmt_time(m.posted_at, tz_name)
    snippet = (m.text or "").replace("\n", " ")
    if len(snippet) > 80:
        snippet = snippet[:77] + "…"
    chan = _channel_label(m)
    link = f"<{m.permalink}|{when}>" if m.permalink else when
    author = f"*{m.author_name}*" if m.author_name else "(unknown)"
    return f"• {author} {chan} @ {link} — {snippet}"


def _fmt_due(due_iso: str | None) -> str:
    if not due_iso:
        return "no due date"
    try:
        return datetime.fromisoformat(due_iso).strftime("due %a %b %-d")
    except Exception:
        return f"due {due_iso}"


def _wrike_line(task: dict) -> str:
    title = task.get("title") or "(untitled)"
    perma = task.get("permalink") or ""
    due = _fmt_due(task.get("due_date"))
    return f"• <{perma}|{title}>  ·  {due}" if perma else f"• {title}  ·  {due}"


def _hms(td: timedelta) -> str:
    total_min = int(td.total_seconds() // 60)
    h, m = divmod(total_min, 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def briefing_blocks(
    *,
    user_name: str,
    today: datetime,
    tz_name: str,
    mentions: list[UnrepliedMention],
    wrike_now: list[dict],
    wrike_due_soon: list[dict],
    calendar_events: list[dict[str, Any]],
    busy_total: timedelta,
    free_total: timedelta,
    free_slots: list[TimeSlot],
) -> list[dict]:
    blocks: list[dict] = [
        _slack_section(f"🌅  *Good morning, {user_name}* — {_fmt_day(today, tz_name)}"),
        {"type": "divider"},
    ]

    # ── Slack ───
    if not mentions:
        blocks.append(_slack_section("📥  *Slack* — 🎉 no unreplied mentions in the last 7 days"))
    else:
        yest, earlier = _bucket_mentions(mentions, today, tz_name)
        lines = [f"📥  *Slack* — *{len(mentions)}* unreplied in the last 7 days"]
        if yest:
            lines.append("\n*Yesterday & today*")
            lines.extend(_mention_line(m, tz_name) for m in yest)
        if earlier:
            lines.append("\n*Earlier this week*")
            lines.extend(_mention_line(m, tz_name) for m in earlier)
        blocks.extend(_section_chunks("\n".join(lines)))

    blocks.append({"type": "divider"})

    # ── Wrike ───
    if not wrike_now and not wrike_due_soon:
        blocks.append(_slack_section("📋  *Wrike* — nothing in 'New' or due in next 2 days"))
    else:
        lines = [
            f"📋  *Wrike* — *{len(wrike_now)}* in `New` · *{len(wrike_due_soon)}* due ≤ 48h"
        ]
        if wrike_now:
            lines.append("\n*New*")
            lines.extend(_wrike_line(t) for t in wrike_now)
        if wrike_due_soon:
            lines.append("\n*Due in next 2 days*")
            lines.extend(_wrike_line(t) for t in wrike_due_soon)
        blocks.extend(_section_chunks("\n".join(lines)))

    blocks.append({"type": "divider"})

    # ── Calendar ───
    cal_header = (
        f"📅  *Calendar* — *{_hms(busy_total)} busy · {_hms(free_total)} free*"
    )
    if not calendar_events:
        blocks.append(_slack_section(f"{cal_header}\nNothing scheduled today."))
    else:
        cal_lines = [cal_header]
        for ev in calendar_events:
            start = ev.get("start") or ""
            end = ev.get("end") or ""
            try:
                s = datetime.fromisoformat(start.replace("Z", "+00:00"))
                e = datetime.fromisoformat(end.replace("Z", "+00:00"))
                ts_label = f"{_fmt_time(s, tz_name)}–{_fmt_time(e, tz_name)}"
            except Exception:
                ts_label = "all day"
            tag = ""
            if ev.get("category") == "blocked":
                tag = "  *[BLOCKED]*"
            cal_lines.append(f"• {ts_label}  {ev.get('title') or '(untitled)'}{tag}")
        if free_slots:
            first = free_slots[0]
            cal_lines.append(
                f"\n_First open block_: {_fmt_time(first.start, tz_name)}–"
                f"{_fmt_time(first.end, tz_name)} ({_hms(first.end - first.start)})"
            )
        blocks.extend(_section_chunks("\n".join(cal_lines)))

    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"_Generated at {_fmt_time(today, tz_name)}_  ·  Run `/connect` to manage integrations",
                }
            ],
        }
    )
    return blocks
