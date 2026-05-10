"""Restricted tool surface for the conversational agent.

Scope (hard rules — enforced both in code and via the system prompt):

  Calendar : list_events  ·  create_event  ·  update_event   (NO delete)
  Wrike    : list_tasks   ·  get_task      ·  update_task_status  ·  post_task_comment
  Slack    : search_unreplied_mentions    (READ-ONLY — never sends/replies as user)

Two-phase approval:
  Every WRITE tool (create/update event, update Wrike status, post Wrike comment)
  takes a `confirmed: bool`. The first call (confirmed=False) returns a preview
  describing what *would* happen — the model must show this to the user, get
  explicit approval, then call again with confirmed=True to actually execute.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from dateutil import parser as dateparser

from app.integrations import google_calendar as gcal
from app.integrations import slack_search
from app.integrations import wrike as wrike_int
from app.utils.timezone import end_of_day, now_in, start_of_day, user_tz


# ── Tool schemas exposed to Claude ─────────────────────────────────────────


TOOL_SCHEMAS: list[dict[str, Any]] = [
    # ─── Calendar (read) ───
    {
        "name": "list_calendar_events",
        "description": "List Google Calendar events for a time range. Read-only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "natural_range": {
                    "type": "string",
                    "enum": ["today", "tomorrow", "this_week", "next_7_days"],
                    "description": "Convenient date range; if used, ignore start/end.",
                },
                "start_iso": {"type": "string", "description": "ISO 8601 start datetime."},
                "end_iso": {"type": "string", "description": "ISO 8601 end datetime."},
            },
        },
    },
    # ─── Calendar (write) ───
    {
        "name": "create_calendar_event",
        "description": (
            "Propose or create a calendar event. Call FIRST with confirmed=false to get a "
            "human-readable preview. After the user approves, call AGAIN with confirmed=true "
            "to actually create the event."
        ),
        "input_schema": {
            "type": "object",
            "required": ["title", "start_iso", "end_iso", "confirmed"],
            "properties": {
                "title": {"type": "string"},
                "start_iso": {"type": "string", "description": "ISO 8601 with timezone."},
                "end_iso": {"type": "string", "description": "ISO 8601 with timezone."},
                "description": {"type": "string", "description": "Optional notes."},
                "confirmed": {
                    "type": "boolean",
                    "description": "false = preview only; true = actually create.",
                },
            },
        },
    },
    {
        "name": "update_calendar_event",
        "description": (
            "Propose or update an existing calendar event. Use list_calendar_events to find "
            "the event_id first. Only the fields you provide are changed; omit a field to "
            "leave it untouched. Call FIRST with confirmed=false for a preview, then with "
            "confirmed=true after user approves. Cannot delete events."
        ),
        "input_schema": {
            "type": "object",
            "required": ["event_id", "confirmed"],
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Calendar event id, from list_calendar_events.",
                },
                "title": {"type": "string", "description": "New event title (optional)."},
                "description": {
                    "type": "string",
                    "description": "New event description / notes (optional).",
                },
                "start_iso": {
                    "type": "string",
                    "description": "New start datetime, ISO 8601 with tz (optional).",
                },
                "end_iso": {
                    "type": "string",
                    "description": "New end datetime, ISO 8601 with tz (optional).",
                },
                "confirmed": {"type": "boolean"},
            },
        },
    },
    # ─── Wrike (read) ───
    {
        "name": "list_wrike_tasks",
        "description": (
            "List Wrike tasks assigned to the user. Read-only. Filter by custom status name "
            "(e.g. 'New', 'In Progress') and/or due_within_days."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status_name": {"type": "string"},
                "due_within_days": {"type": "integer"},
            },
        },
    },
    {
        "name": "get_wrike_task",
        "description": "Fetch full details of one Wrike task by id. Read-only.",
        "input_schema": {
            "type": "object",
            "required": ["task_id"],
            "properties": {"task_id": {"type": "string"}},
        },
    },
    # ─── Wrike (write — restricted) ───
    {
        "name": "update_wrike_task_status",
        "description": (
            "Propose or change the custom status of a Wrike task. Call FIRST with "
            "confirmed=false for a preview. After user approves, call again with "
            "confirmed=true. Cannot create, rename, or delete tasks — status only."
        ),
        "input_schema": {
            "type": "object",
            "required": ["task_id", "new_status_name", "confirmed"],
            "properties": {
                "task_id": {"type": "string"},
                "new_status_name": {
                    "type": "string",
                    "description": "Target status name as it appears in Wrike, e.g. 'In Progress'.",
                },
                "confirmed": {"type": "boolean"},
            },
        },
    },
    {
        "name": "post_wrike_task_comment",
        "description": (
            "Propose or post a comment on a Wrike task. Call FIRST with confirmed=false "
            "to preview the exact text. After user approves, call again with confirmed=true."
        ),
        "input_schema": {
            "type": "object",
            "required": ["task_id", "text", "confirmed"],
            "properties": {
                "task_id": {"type": "string"},
                "text": {"type": "string"},
                "confirmed": {"type": "boolean"},
            },
        },
    },
    # ─── Slack (read-only) ───
    {
        "name": "search_unreplied_mentions",
        "description": (
            "Read-only: search Slack for the user's @-mentions in the last N days that "
            "they haven't replied to. CANNOT send messages or reply on the user's behalf."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Lookback window in days. Default 7. Max 30.",
                },
            },
        },
    },
]


# ── Dispatcher ─────────────────────────────────────────────────────────────


async def dispatch_tool(
    name: str,
    inputs: dict[str, Any],
    *,
    user_id: int,
    user_tz: str,
    workday_start: str,
    workday_end: str,
    slack_user_id: str | None = None,
    slack_bot_token: str | None = None,
) -> Any:
    if name == "list_calendar_events":
        return await _list_calendar_events(inputs, user_id=user_id, tz_name=user_tz)
    if name == "create_calendar_event":
        return await _create_calendar_event(inputs, user_id=user_id, tz_name=user_tz)
    if name == "update_calendar_event":
        return await _update_calendar_event(inputs, user_id=user_id, tz_name=user_tz)
    if name == "list_wrike_tasks":
        return await _list_wrike_tasks(inputs, user_id=user_id)
    if name == "get_wrike_task":
        return await _get_wrike_task(inputs, user_id=user_id)
    if name == "update_wrike_task_status":
        return await _update_wrike_task_status(inputs, user_id=user_id)
    if name == "post_wrike_task_comment":
        return await _post_wrike_task_comment(inputs, user_id=user_id)
    if name == "search_unreplied_mentions":
        return await _search_unreplied_mentions(
            inputs, user_id=user_id, slack_user_id=slack_user_id, bot_token=slack_bot_token
        )
    raise ValueError(f"Unknown / disallowed tool: {name}")


# ── Calendar implementations ───────────────────────────────────────────────


async def _list_calendar_events(inputs: dict, *, user_id: int, tz_name: str) -> dict:
    natural = inputs.get("natural_range")
    if natural:
        now_user = now_in(tz_name)
        if natural == "today":
            time_min, time_max = start_of_day(now_user, tz_name), end_of_day(now_user, tz_name)
        elif natural == "tomorrow":
            d = now_user + timedelta(days=1)
            time_min, time_max = start_of_day(d, tz_name), end_of_day(d, tz_name)
        elif natural == "this_week":
            time_min = start_of_day(now_user, tz_name)
            time_max = end_of_day(
                now_user + timedelta(days=7 - now_user.weekday()), tz_name
            )
        elif natural == "next_7_days":
            time_min = start_of_day(now_user, tz_name)
            time_max = end_of_day(now_user + timedelta(days=7), tz_name)
        else:
            return {"error": f"unknown natural_range {natural!r}"}
    else:
        if not inputs.get("start_iso") or not inputs.get("end_iso"):
            return {"error": "Provide natural_range or both start_iso and end_iso."}
        time_min = _parse_dt(inputs["start_iso"], tz_name)
        time_max = _parse_dt(inputs["end_iso"], tz_name)

    events = await gcal.list_events(user_id, time_min=time_min, time_max=time_max)
    summary = []
    for ev in events:
        summary.append(
            {
                "id": ev.get("id"),
                "title": ev.get("summary"),
                "start": ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date"),
                "end": ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date"),
                "attendee_count": len(ev.get("attendees") or []),
                "is_all_day": gcal.event_is_all_day(ev),
            }
        )
    return {
        "range": {"start": time_min.isoformat(), "end": time_max.isoformat()},
        "count": len(summary),
        "events": summary,
    }


async def _create_calendar_event(inputs: dict, *, user_id: int, tz_name: str) -> dict:
    title = inputs.get("title", "").strip()
    start = _parse_dt(inputs["start_iso"], tz_name)
    end = _parse_dt(inputs["end_iso"], tz_name)
    description = inputs.get("description", "")
    confirmed = bool(inputs.get("confirmed"))

    if not confirmed:
        return {
            "preview": True,
            "summary_for_user": (
                f"Create a calendar event titled “{title}” from "
                f"{start.strftime('%a %b %-d, %-I:%M%p')} to "
                f"{end.strftime('%-I:%M%p')} ({tz_name})."
            ),
            "next_action": (
                "If the user approves, call create_calendar_event again with the same "
                "arguments and confirmed=true."
            ),
        }

    event = await gcal.create_event(
        user_id,
        summary=title,
        description=description,
        start=start,
        end=end,
        tz_name=tz_name,
    )
    return {
        "ok": True,
        "id": event.get("id"),
        "html_link": event.get("htmlLink"),
        "message": f"Created event {title!r}.",
    }


async def _update_calendar_event(inputs: dict, *, user_id: int, tz_name: str) -> dict:
    event_id = inputs["event_id"]
    confirmed = bool(inputs.get("confirmed"))

    # Look up the existing event so previews and PATCH bodies are accurate.
    try:
        ev = await gcal.get_event(user_id, event_id=event_id)
    except Exception as exc:
        return {"error": f"Could not fetch event {event_id}: {exc}"}

    cur_title = ev.get("summary") or "(untitled)"
    cur_start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date")
    cur_end = ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date")

    new_title = inputs.get("title")
    new_description = inputs.get("description")
    new_start = _parse_dt(inputs["start_iso"], tz_name) if inputs.get("start_iso") else None
    new_end = _parse_dt(inputs["end_iso"], tz_name) if inputs.get("end_iso") else None

    if not any([new_title, new_description, new_start, new_end]):
        return {
            "error": "Nothing to update — provide at least one of title, description, "
            "start_iso, or end_iso."
        }

    if not confirmed:
        changes = []
        if new_title and new_title != cur_title:
            changes.append(f"title: “{cur_title}” → “{new_title}”")
        if new_description is not None:
            changes.append("update description")
        if new_start:
            changes.append(f"start: {cur_start} → {new_start.isoformat()}")
        if new_end:
            changes.append(f"end: {cur_end} → {new_end.isoformat()}")
        return {
            "preview": True,
            "summary_for_user": (
                f"Update the event “{cur_title}”:\n  • " + "\n  • ".join(changes)
            ),
            "next_action": (
                "If the user approves, call update_calendar_event again with the same "
                "event_id and confirmed=true."
            ),
        }

    updated = await gcal.update_event(
        user_id,
        event_id=event_id,
        summary=new_title,
        description=new_description,
        start=new_start,
        end=new_end,
        tz_name=tz_name,
    )
    return {
        "ok": True,
        "id": updated.get("id"),
        "html_link": updated.get("htmlLink"),
        "message": f"Updated event {cur_title!r}.",
    }


# ── Wrike implementations ──────────────────────────────────────────────────


async def _list_wrike_tasks(inputs: dict, *, user_id: int) -> dict:
    client = await wrike_int.WrikeClient.for_user(user_id)
    me = await client.me()
    me_id = me.get("id")
    if not me_id:
        return {"error": "Wrike contact lookup failed"}

    status_name = inputs.get("status_name")
    due_within_days = inputs.get("due_within_days")

    custom_statuses: list[str] = []
    if status_name:
        custom_statuses = await wrike_int.resolve_status_ids(user_id, status_name)
        if not custom_statuses:
            return {"error": f"No custom status named {status_name!r} in this workspace."}

    fields = ["responsibleIds", "description"]
    main_tasks = await client.tasks(
        responsibles=[me_id],
        custom_statuses=custom_statuses or None,
        fields=fields,
    )

    extra: list[dict] = []
    if due_within_days:
        now = datetime.now(tz=user_tz(None))
        soon = await client.tasks(
            responsibles=[me_id],
            due_date_start=now,
            due_date_end=now + timedelta(days=int(due_within_days)),
            status="Active",
            fields=fields,
        )
        seen_ids = {t["id"] for t in main_tasks}
        extra = [t for t in soon if t["id"] not in seen_ids]

    return {
        "tasks": [_summarize_task(t) for t in main_tasks],
        "due_soon": [_summarize_task(t) for t in extra],
    }


async def _get_wrike_task(inputs: dict, *, user_id: int) -> dict:
    client = await wrike_int.WrikeClient.for_user(user_id)
    t = await client.task(inputs["task_id"], fields=["description", "responsibleIds"])
    if not t:
        return {"error": f"Wrike task {inputs['task_id']!r} not found."}
    return _summarize_task(t)


async def _update_wrike_task_status(inputs: dict, *, user_id: int) -> dict:
    task_id = inputs["task_id"]
    new_name = inputs["new_status_name"]
    confirmed = bool(inputs.get("confirmed"))

    client = await wrike_int.WrikeClient.for_user(user_id)
    task = await client.task(task_id)
    if not task:
        return {"error": f"Wrike task {task_id!r} not found."}

    new_ids = await wrike_int.resolve_status_ids(user_id, new_name)
    if not new_ids:
        return {"error": f"No status named {new_name!r} in this workspace."}

    # Choose the status ID that lives in the SAME workflow as the task.
    target_id = new_ids[0]
    task_status_id = task.get("customStatusId")
    if task_status_id and len(new_ids) > 1:
        # Best-effort match; if we can't, fall back to first.
        target_id = new_ids[0]

    if not confirmed:
        return {
            "preview": True,
            "summary_for_user": (
                f"Change Wrike task “{task.get('title')}” to status “{new_name}”."
            ),
            "next_action": (
                "If the user approves, call update_wrike_task_status again with "
                "confirmed=true."
            ),
        }

    await client.update_task_status(task_id, custom_status_id=target_id)
    return {"ok": True, "message": f"Updated {task.get('title')!r} → {new_name}."}


async def _post_wrike_task_comment(inputs: dict, *, user_id: int) -> dict:
    task_id = inputs["task_id"]
    text = inputs["text"].strip()
    confirmed = bool(inputs.get("confirmed"))

    client = await wrike_int.WrikeClient.for_user(user_id)
    task = await client.task(task_id)
    if not task:
        return {"error": f"Wrike task {task_id!r} not found."}

    if not confirmed:
        return {
            "preview": True,
            "summary_for_user": (
                f"Post comment on “{task.get('title')}”:\n\n> {text}"
            ),
            "next_action": (
                "If the user approves, call post_wrike_task_comment again with the "
                "same text and confirmed=true."
            ),
        }

    await client.post_task_comment(task_id, text=text)
    return {"ok": True, "message": f"Comment posted on {task.get('title')!r}."}


# ── Slack (read-only) implementation ───────────────────────────────────────


async def _search_unreplied_mentions(
    inputs: dict, *, user_id: int, slack_user_id: str | None, bot_token: str | None
) -> dict:
    if not slack_user_id or not bot_token:
        return {"error": "Slack search not available in this context."}
    days = min(int(inputs.get("days", 7)), 30)
    try:
        mentions = await slack_search.find_unreplied_mentions(
            user_id=user_id,
            slack_user_id=slack_user_id,
            days=days,
            bot_token=bot_token,
        )
    except slack_search.SlackUserNotConnectedError as exc:
        return {"error": str(exc)}
    return {
        "count": len(mentions),
        "mentions": [
            {
                "from": m.author_name,
                "channel": m.channel_name,
                "is_dm": m.is_dm,
                "text": (m.text or "")[:300],
                "posted_at": m.posted_at.isoformat(),
                "permalink": m.permalink,
            }
            for m in mentions
        ],
    }


# ── Helpers ────────────────────────────────────────────────────────────────


def _summarize_task(t: dict) -> dict:
    due = (t.get("dates") or {}).get("due")
    return {
        "id": t.get("id"),
        "title": t.get("title"),
        "due_date": due,
        "status": (t.get("customStatusName") or t.get("status") or ""),
        "permalink": wrike_int.task_permalink(t),
    }


def _parse_dt(s: str, tz_name: str) -> datetime:
    dt = dateparser.parse(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=user_tz(tz_name))
    return dt
