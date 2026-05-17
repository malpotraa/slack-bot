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
from app.utils.timezone import (
    end_of_day,
    fmt_local_range,
    fmt_local_time,
    now_in,
    start_of_day,
    user_tz,
)
from app.utils.working_hours import first_free_slot_for_duration


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
        "description": (
            "Fetch full details of one Wrike task. Read-only. Accept EITHER an "
            "alphanumeric API id (like 'IEAA4BCD') in `task_id` OR a Wrike URL "
            "the user pasted (like 'https://www.wrike.com/workspace.htm?...id=4449467731...') "
            "in `task_ref`. Always prefer `task_ref` when the user provides a URL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": (
                        "Alphanumeric Wrike API task id (e.g. 'IEAA4BCD'). "
                        "DO NOT pass the numeric id from a Wrike URL "
                        "(e.g. '4449467731') — that is not the API id; "
                        "use `task_ref` for URLs."
                    ),
                },
                "task_ref": {
                    "type": "string",
                    "description": (
                        "Any Wrike URL the user pasted. Will be resolved server-side "
                        "to the API task id."
                    ),
                },
            },
        },
    },
    # ─── Wrike (write — restricted) ───
    {
        "name": "update_wrike_task_status",
        "description": (
            "Propose or change the custom status of a Wrike task. Call FIRST with "
            "confirmed=false for a preview. After user approves, call again with "
            "confirmed=true. Cannot create, rename, or delete tasks — status only. "
            "Accept EITHER an alphanumeric `task_id` OR a Wrike URL in `task_ref`."
        ),
        "input_schema": {
            "type": "object",
            "required": ["new_status_name", "confirmed"],
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": (
                        "Alphanumeric Wrike API task id (e.g. 'IEAA4BCD'). "
                        "DO NOT pass the numeric id from a Wrike URL "
                        "(e.g. '4449467731') — that is not the API id; "
                        "use `task_ref` for URLs."
                    ),
                },
                "task_ref": {
                    "type": "string",
                    "description": "Wrike URL the user pasted (resolved server-side).",
                },
                "new_status_name": {
                    "type": "string",
                    "description": "Target status name as it appears in Wrike, e.g. 'In Progress', 'Completed'.",
                },
                "confirmed": {"type": "boolean"},
            },
        },
    },
    {
        "name": "post_wrike_task_comment",
        "description": (
            "Propose or post a comment on a Wrike task. Call FIRST with confirmed=false "
            "to preview the exact text. After user approves, call again with confirmed=true. "
            "Accept EITHER `task_id` OR a Wrike URL in `task_ref`."
        ),
        "input_schema": {
            "type": "object",
            "required": ["text", "confirmed"],
            "properties": {
                "task_id": {"type": "string"},
                "task_ref": {"type": "string"},
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
    # ─── Internal: drives /wrike approval-button card ───
    {
        "name": "schedule_wrike_task",
        "description": (
            "Plot a Wrike task onto Google Calendar and mark the task as "
            "'Accepted & Scheduled' in Wrike. Used by the /wrike approval card. "
            "Atomic from the user's perspective: one click does both. "
            "Call with confirmed=false for preview, then confirmed=true to execute."
        ),
        "input_schema": {
            "type": "object",
            "required": ["task_id", "title", "start_iso", "end_iso", "tz_name", "confirmed"],
            "properties": {
                "task_id": {"type": "string", "description": "Wrike alphanumeric task id."},
                "title": {"type": "string"},
                "permalink": {"type": "string"},
                "description_extra": {"type": "string"},
                "start_iso": {"type": "string"},
                "end_iso": {"type": "string"},
                "tz_name": {"type": "string"},
                "confirmed": {"type": "boolean"},
            },
        },
    },
    # ─── User settings (meta) ───
    {
        "name": "update_working_hours",
        "description": (
            "Set the user's working-hours window (HH:MM 24-hour). Used by "
            "/goodmorning and /wrike to compute busy/free time. Single window "
            "applied to every day (per-day support is a future enhancement). "
            "Call FIRST with confirmed=false for preview, then confirmed=true."
        ),
        "input_schema": {
            "type": "object",
            "required": ["work_start", "work_end", "confirmed"],
            "properties": {
                "work_start": {
                    "type": "string",
                    "description": "Start of workday, HH:MM 24-hour. e.g. '08:00'.",
                },
                "work_end": {
                    "type": "string",
                    "description": "End of workday, HH:MM 24-hour. e.g. '16:00'.",
                },
                "confirmed": {"type": "boolean"},
            },
        },
    },
    {
        "name": "get_working_hours",
        "description": "Read the user's currently saved working-hours window.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_user_notes",
        "description": (
            "Read the user's saved free-form notes / preferences "
            "(e.g. 'I like 30-min focus blocks'). Read-only."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "update_user_notes",
        "description": (
            "Save / replace the user's free-form notes (preferences, "
            "context, reminders for the assistant). The new text REPLACES "
            "any existing notes — to add to existing notes, call "
            "get_user_notes first and pass the combined text. Pass an "
            "empty string to clear. Call FIRST with confirmed=false for "
            "a preview, then confirmed=true after the user approves."
        ),
        "input_schema": {
            "type": "object",
            "required": ["notes", "confirmed"],
            "properties": {
                "notes": {
                    "type": "string",
                    "description": "Full replacement text (up to ~1000 chars). Empty to clear.",
                },
                "confirmed": {"type": "boolean"},
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
        return await _create_calendar_event(
            inputs,
            user_id=user_id,
            tz_name=user_tz,
            workday_start=workday_start,
            workday_end=workday_end,
        )
    if name == "update_calendar_event":
        return await _update_calendar_event(
            inputs,
            user_id=user_id,
            tz_name=user_tz,
            workday_start=workday_start,
            workday_end=workday_end,
        )
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
    if name == "update_working_hours":
        return await _update_working_hours(inputs, user_id=user_id)
    if name == "get_working_hours":
        return await _get_working_hours(user_id=user_id)
    if name == "get_user_notes":
        return await _get_user_notes(user_id=user_id)
    if name == "update_user_notes":
        return await _update_user_notes(inputs, user_id=user_id)
    if name == "schedule_wrike_task":
        return await _schedule_wrike_task(inputs, user_id=user_id)
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


async def _conflict_alternate(
    *,
    user_id: int,
    tz_name: str,
    workday_start: str,
    workday_end: str,
    start: datetime,
    end: datetime,
    title: str,
    base_args: dict,
    tool_name: str,
) -> dict | None:
    """If [start, end] overlaps existing events, return a pending-action dict
    that proposes the next free slot of the same duration on/after the
    requested start. Returns None if no alternative could be found.
    """
    duration_min = max(int((end - start).total_seconds() // 60), 15)
    busy = await gcal.busy_slots_in_horizon(user_id, start, days=5)
    alt = first_free_slot_for_duration(
        busy,
        duration_minutes=duration_min,
        horizon_days=5,
        workday_start=workday_start,
        workday_end=workday_end,
        tz_name=tz_name,
        not_before=start,
    )
    if alt is None:
        return None
    alt_args = dict(base_args)
    alt_args["start_iso"] = alt.start.isoformat()
    alt_args["end_iso"] = alt.end.isoformat()
    alt_args["confirmed"] = True
    return {
        "tool": tool_name,
        "args": alt_args,
        "summary": f"Suggested: *{fmt_local_range(alt.start, alt.end, tz_name)}*",
        "short_time": fmt_local_time(alt.start, tz_name),
    }


async def _create_calendar_event(
    inputs: dict,
    *,
    user_id: int,
    tz_name: str,
    workday_start: str,
    workday_end: str,
) -> dict:
    title = inputs.get("title", "").strip()
    start = _parse_dt(inputs["start_iso"], tz_name)
    end = _parse_dt(inputs["end_iso"], tz_name)
    description = inputs.get("description", "")
    confirmed = bool(inputs.get("confirmed"))

    if not confirmed:
        when = fmt_local_range(start, end, tz_name)
        lines = [f"📅 Create *{title}* — *{when}*"]

        # Conflict check
        overlaps = await gcal.find_overlaps(user_id, start, end)
        alternate: dict | None = None
        if overlaps:
            ov = overlaps[0]
            ov_when = _format_overlap_when(ov, tz_name)
            ov_title = ov.get("title") or "(untitled)"
            extra = f" (+{len(overlaps) - 1} more)" if len(overlaps) > 1 else ""
            lines.append("")
            lines.append(f"⚠️ Overlaps *{ov_title}* ({ov_when}){extra}")

            alternate = await _conflict_alternate(
                user_id=user_id,
                tz_name=tz_name,
                workday_start=workday_start,
                workday_end=workday_end,
                start=start,
                end=end,
                title=title,
                base_args={
                    "title": title,
                    "description": description,
                    "start_iso": inputs["start_iso"],
                    "end_iso": inputs["end_iso"],
                },
                tool_name="create_calendar_event",
            )

        result: dict = {
            "preview": True,
            "summary_for_user": "\n".join(lines),
            "next_action": (
                "Reply with ONE short sentence stating intent — do NOT echo "
                "the times, conflicts, or alternate. The approval card shows all of that."
            ),
        }
        if alternate is not None:
            result["alternate"] = alternate
        return result

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


async def _update_calendar_event(
    inputs: dict,
    *,
    user_id: int,
    tz_name: str,
    workday_start: str,
    workday_end: str,
) -> dict:
    event_id = inputs["event_id"]
    confirmed = bool(inputs.get("confirmed"))

    # Look up the existing event so previews and PATCH bodies are accurate.
    try:
        ev = await gcal.get_event(user_id, event_id=event_id)
    except Exception as exc:
        return {"error": f"Could not fetch event {event_id}: {exc}"}

    cur_title = ev.get("summary") or "(untitled)"
    cur_start_iso = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date")
    cur_end_iso = ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date")

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
        time_changed = bool(new_start or new_end)
        title_changed = bool(new_title and new_title != cur_title)

        # Pick a verb for the header line — "Move" reads better than "Update"
        # when only the time changes; "Rename" when only title; otherwise "Update".
        if time_changed and not title_changed and new_description is None:
            verb = "Move"
        elif title_changed and not time_changed and new_description is None:
            verb = "Rename"
        else:
            verb = "Update"

        lines: list[str] = [f"📅 {verb} *{cur_title}*"]

        if time_changed:
            try:
                cur_s = _parse_dt(cur_start_iso, tz_name)
                cur_e = _parse_dt(cur_end_iso, tz_name)
                cur_str = fmt_local_range(cur_s, cur_e, tz_name)
            except Exception:
                cur_str = f"{cur_start_iso} – {cur_end_iso}"
            try:
                effective_start = new_start or _parse_dt(cur_start_iso, tz_name)
                effective_end = new_end or _parse_dt(cur_end_iso, tz_name)
                new_str = fmt_local_range(effective_start, effective_end, tz_name)
            except Exception:
                effective_start = effective_end = None
                new_str = ""
            lines.append(f"*{cur_str}*  →  *{new_str}*" if new_str else f"*{cur_str}*")

        if title_changed:
            lines.append(f"Title → *{new_title}*")
        if new_description is not None:
            lines.append("Description will be updated.")

        # Conflict check — only when time actually changes.
        alternate: dict | None = None
        if time_changed:
            try:
                effective_start = new_start or _parse_dt(cur_start_iso, tz_name)
                effective_end = new_end or _parse_dt(cur_end_iso, tz_name)
            except Exception:
                effective_start = effective_end = None

            if effective_start and effective_end:
                overlaps = await gcal.find_overlaps(
                    user_id,
                    effective_start,
                    effective_end,
                    exclude_event_id=event_id,
                )
                if overlaps:
                    ov = overlaps[0]
                    ov_when = _format_overlap_when(ov, tz_name)
                    ov_title = ov.get("title") or "(untitled)"
                    extra = f" (+{len(overlaps) - 1} more)" if len(overlaps) > 1 else ""
                    lines.append("")
                    lines.append(f"⚠️ Overlaps *{ov_title}* ({ov_when}){extra}")

                    base_args = {
                        "event_id": event_id,
                        "title": new_title,
                        "description": new_description,
                        "start_iso": (new_start or effective_start).isoformat(),
                        "end_iso": (new_end or effective_end).isoformat(),
                    }
                    base_args = {k: v for k, v in base_args.items() if v is not None}
                    alternate = await _conflict_alternate(
                        user_id=user_id,
                        tz_name=tz_name,
                        workday_start=workday_start,
                        workday_end=workday_end,
                        start=effective_start,
                        end=effective_end,
                        title=cur_title,
                        base_args=base_args,
                        tool_name="update_calendar_event",
                    )

        result: dict = {
            "preview": True,
            "summary_for_user": "\n".join(lines),
            "next_action": (
                "Reply with ONE short sentence stating intent — do NOT echo "
                "the times, conflicts, or alternate. The approval card shows all of that."
            ),
        }
        if alternate is not None:
            result["alternate"] = alternate
        return result

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


async def _resolve_wrike_task(
    client: "wrike_int.WrikeClient", inputs: dict
) -> dict | None:
    """Look up a Wrike task using whichever ref the LLM supplied (id or URL)."""
    task_id = (inputs.get("task_id") or "").strip()
    task_ref = (inputs.get("task_ref") or "").strip()
    if task_ref:
        return await client.resolve_task_ref(task_ref)
    if task_id:
        # If the LLM passed a URL into task_id by mistake, still handle it.
        return await client.resolve_task_ref(task_id)
    return None


async def _get_wrike_task(inputs: dict, *, user_id: int) -> dict:
    client = await wrike_int.WrikeClient.for_user(user_id)
    t = await _resolve_wrike_task(client, inputs)
    if not t:
        return {
            "error": (
                "Wrike task not found. Pass either an alphanumeric API id "
                "(e.g. 'IEAA4BCD') as `task_id`, or a Wrike URL as `task_ref`."
            )
        }
    return _summarize_task(t)


async def _update_wrike_task_status(inputs: dict, *, user_id: int) -> dict:
    new_name = inputs["new_status_name"]
    confirmed = bool(inputs.get("confirmed"))

    client = await wrike_int.WrikeClient.for_user(user_id)
    task = await _resolve_wrike_task(client, inputs)
    if not task:
        return {
            "error": (
                "Wrike task not found. Pass either an alphanumeric API id "
                "as `task_id`, or a Wrike URL as `task_ref`."
            )
        }

    new_ids = await wrike_int.resolve_status_ids(user_id, new_name)
    if not new_ids:
        return {"error": f"No status named {new_name!r} in this workspace."}

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
            "_resolved_task_id": task.get("id"),
        }

    await client.update_task_status(task["id"], custom_status_id=target_id)
    return {"ok": True, "message": f"Updated {task.get('title')!r} → {new_name}."}


async def _post_wrike_task_comment(inputs: dict, *, user_id: int) -> dict:
    text = (inputs.get("text") or "").strip()
    confirmed = bool(inputs.get("confirmed"))
    if not text:
        return {"error": "Comment text is empty."}

    client = await wrike_int.WrikeClient.for_user(user_id)
    task = await _resolve_wrike_task(client, inputs)
    if not task:
        return {
            "error": (
                "Wrike task not found. Pass either an alphanumeric API id "
                "as `task_id`, or a Wrike URL as `task_ref`."
            )
        }

    if not confirmed:
        return {
            "preview": True,
            "summary_for_user": (
                f"Post this comment on “{task.get('title')}”:\n> {text}"
            ),
            "next_action": (
                "If the user approves, call post_wrike_task_comment again with the "
                "same text and confirmed=true."
            ),
            "_resolved_task_id": task.get("id"),
        }

    await client.post_task_comment(task["id"], text=text)
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


# ── /wrike scheduler (event + status update) ───────────────────────────────


SCHEDULED_STATUS_NAME = "Accepted & Scheduled"


async def _schedule_wrike_task(inputs: dict, *, user_id: int) -> dict:
    """Create a calendar event for a Wrike task AND set the task's status
    to 'Accepted & Scheduled'. Atomic from the user's perspective.
    """
    from datetime import datetime as _dt

    task_id = (inputs.get("task_id") or "").strip()
    title = (inputs.get("title") or "").strip()
    permalink = (inputs.get("permalink") or "").strip()
    description_extra = (inputs.get("description_extra") or "").strip()
    tz_name = inputs.get("tz_name") or "UTC"
    confirmed = bool(inputs.get("confirmed"))

    if not task_id or not title:
        return {"error": "Missing task_id or title."}
    if not inputs.get("start_iso") or not inputs.get("end_iso"):
        return {"error": "Missing start_iso / end_iso."}

    try:
        start = _parse_dt(inputs["start_iso"], tz_name)
        end = _parse_dt(inputs["end_iso"], tz_name)
    except Exception as exc:
        return {"error": f"Could not parse times: {exc}"}

    if not confirmed:
        return {
            "preview": True,
            "summary_for_user": (
                f"Schedule *{title}* — "
                f"{start.strftime('%a %b %-d, %-I:%M%p')}–"
                f"{end.strftime('%-I:%M%p')}. "
                f"Also marks the Wrike task as *{SCHEDULED_STATUS_NAME}*."
            ),
            "next_action": "On approval, runs with confirmed=true.",
        }

    description = (
        f"Wrike task: {title}\n"
        f"Permalink: {permalink}\n\n"
        f"{description_extra}\n\n"
        f"— Scheduled via Slack /wrike at {_dt.utcnow().isoformat()}Z"
    )

    # 1. Create the calendar event
    try:
        event = await gcal.create_event(
            user_id,
            summary=title,
            description=description,
            start=start,
            end=end,
            tz_name=tz_name,
            extended_properties={"wrikeTaskId": task_id, "createdBy": "slack-assistant"},
            source_title="Wrike task",
            source_url=permalink,
        )
    except Exception as exc:
        return {"error": f"Failed to create Calendar event: {exc}"}

    # 2. Update Wrike task status — best-effort; calendar event already created
    status_msg = ""
    try:
        new_ids = await wrike_int.resolve_status_ids(user_id, SCHEDULED_STATUS_NAME)
        if not new_ids:
            status_msg = (
                f" (couldn't update Wrike: status {SCHEDULED_STATUS_NAME!r} not "
                f"found in your workspace)"
            )
        else:
            client = await wrike_int.WrikeClient.for_user(user_id)
            await client.update_task_status(task_id, custom_status_id=new_ids[0])
    except Exception as exc:
        status_msg = f" (Calendar event created; Wrike status update failed: {exc})"

    return {
        "ok": True,
        "event_id": event.get("id"),
        "html_link": event.get("htmlLink"),
        "message": f"Scheduled *{title}* and marked as {SCHEDULED_STATUS_NAME}.{status_msg}",
    }


# ── User settings ──────────────────────────────────────────────────────────


import re as _re

_HHMM = _re.compile(r"^(?P<h>\d{1,2}):(?P<m>\d{2})$")


def _normalize_hhmm(s: str) -> str | None:
    """'8:00' / '08:00' / '16:00' → 'HH:MM' 24-hour. Returns None if invalid."""
    if not s:
        return None
    m = _HHMM.match(s.strip())
    if not m:
        return None
    h = int(m.group("h"))
    minute = int(m.group("m"))
    if not (0 <= h <= 23 and 0 <= minute <= 59):
        return None
    return f"{h:02d}:{minute:02d}"


async def _update_working_hours(inputs: dict, *, user_id: int) -> dict:
    work_start = _normalize_hhmm(inputs.get("work_start") or "")
    work_end = _normalize_hhmm(inputs.get("work_end") or "")
    confirmed = bool(inputs.get("confirmed"))

    if not work_start or not work_end:
        return {
            "error": (
                "Times must be HH:MM 24-hour, e.g. '08:00' and '16:00'. "
                "Please rephrase with valid times."
            )
        }
    if work_start >= work_end:
        return {"error": "Start time must be earlier than end time."}

    if not confirmed:
        return {
            "preview": True,
            "summary_for_user": (
                f"Update your working hours to {work_start}–{work_end}. "
                "This affects /goodmorning and /wrike free-slot math."
            ),
            "next_action": (
                "If the user approves, call update_working_hours again with the "
                "same arguments and confirmed=true."
            ),
        }

    from app.db.engine import session_scope
    from app.db.models import User

    async with session_scope() as session:
        user = await session.get(User, user_id)
        if user is None:
            return {"error": "User not found."}
        user.workday_start = work_start
        user.workday_end = work_end
        session.add(user)

    return {
        "ok": True,
        "work_start": work_start,
        "work_end": work_end,
        "message": f"Working hours saved: {work_start}–{work_end}.",
    }


async def _get_working_hours(*, user_id: int) -> dict:
    from app.db.engine import session_scope
    from app.db.models import User

    async with session_scope() as session:
        user = await session.get(User, user_id)
        if user is None:
            return {"error": "User not found."}
        return {"work_start": user.workday_start, "work_end": user.workday_end}


_USER_NOTES_MAX_CHARS = 1000


async def _get_user_notes(*, user_id: int) -> dict:
    from app.db.engine import session_scope
    from app.db.models import User

    async with session_scope() as session:
        user = await session.get(User, user_id)
        if user is None:
            return {"error": "User not found."}
        return {"notes": user.notes or ""}


async def _update_user_notes(inputs: dict, *, user_id: int) -> dict:
    raw = inputs.get("notes")
    if raw is None:
        return {"error": "Missing `notes`."}
    notes = str(raw).strip()
    if len(notes) > _USER_NOTES_MAX_CHARS:
        return {
            "error": (
                f"Notes too long ({len(notes)} chars). "
                f"Keep under {_USER_NOTES_MAX_CHARS} characters."
            )
        }
    confirmed = bool(inputs.get("confirmed"))

    if not confirmed:
        if not notes:
            summary = "Clear your saved notes."
        else:
            preview = notes if len(notes) <= 200 else notes[:200] + "…"
            summary = f"Save new notes:\n> {preview}"
        return {
            "preview": True,
            "summary_for_user": summary,
            "next_action": (
                "On approval, call update_user_notes again with the same "
                "text and confirmed=true."
            ),
        }

    from app.db.engine import session_scope
    from app.db.models import User

    async with session_scope() as session:
        user = await session.get(User, user_id)
        if user is None:
            return {"error": "User not found."}
        user.notes = notes or None
        session.add(user)

    msg = "Notes cleared." if not notes else "Notes saved."
    return {"ok": True, "message": msg}


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


def _format_overlap_when(ov: dict, tz_name: str) -> str:
    """Format an overlap event's time range for inline display in a preview."""
    try:
        s = _parse_dt(ov["start"], tz_name)
        e = _parse_dt(ov["end"], tz_name)
    except Exception:
        return ""
    # Drop the date prefix — the surrounding line already establishes "this day".
    return f"{fmt_local_time(s, tz_name)}–{fmt_local_time(e, tz_name)}"
