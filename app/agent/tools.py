"""Restricted tool surface for the conversational agent.

Scope (hard rules — enforced both in code and via the system prompt):

  Calendar : list_events  ·  create_event  ·  update_event   (NO delete)
  GoogleAds: fetch KPI reports only after user approval
  Wrike    : list_tasks   ·  get_task      ·  update_task_status  ·  post_task_comment
  Slack    : search_unreplied_mentions    (READ-ONLY — never sends/replies as user)

Two-phase approval:
  Every WRITE tool (create/update event, update Wrike status, post Wrike comment)
  takes a `confirmed: bool`. The first call (confirmed=False) returns a preview
  describing what *would* happen — the model must show this to the user, get
  explicit approval, then call again with confirmed=True to actually execute.
"""

from __future__ import annotations

import asyncio
import re as _re
from datetime import date, datetime, timedelta
from typing import Any

from dateutil import parser as dateparser
from loguru import logger

from app.integrations import google_calendar as gcal
from app.integrations import google_ads
from app.integrations import google_ads_drill
from app.integrations import google_ads_snapshot
from app.integrations import slack_search
from app.integrations import wrike as wrike_int
from app.formatters import ads_blocks
from app.formatters import ads_charts
from app.formatters import kpi_blocks
from app.llm.kpi_summary import explain_kpi_report
from app.utils.timezone import (
    end_of_day,
    fmt_local_range,
    fmt_local_time,
    now_in,
    start_of_day,
    user_tz,
)
from app.utils.slack_mrkdwn import escape_slack_text, quote_slack_text
from app.utils.working_hours import TimeSlot, first_free_slot_for_duration


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
    # ─── Google Ads (read-only data) ───
    {
        "name": "get_google_ads_data",
        "description": (
            "Fetch Google Ads performance data for ONE ad account and post a "
            "table and/or chart. READ-ONLY — never changes the account. Works "
            "for every campaign type (Search, Shopping, Performance Max, "
            "Demand Gen, Video, Display).\n"
            "• Use scope='account_overview' for the first / surface question "
            "about an account.\n"
            "• Use a drill scope (campaign_detail, keywords, search_terms, "
            "ad_groups, ads, products, asset_groups) with campaign_name or "
            "campaign_id when the user digs into one campaign.\n"
            "Pass customer_id when the thread context already names the "
            "account, otherwise account_query. After it returns, narrate the "
            "insight from the returned fact_pack in a few sentences — the "
            "table and/or charts are posted to the user automatically."
        ),
        "input_schema": {
            "type": "object",
            "required": ["scope"],
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": [
                        "account_overview",
                        "campaign_detail",
                        "keywords",
                        "search_terms",
                        "ad_groups",
                        "ads",
                        "products",
                        "asset_groups",
                    ],
                    "description": (
                        "account_overview = surface view of the whole account; "
                        "the others drill into ONE campaign."
                    ),
                },
                "customer_id": {
                    "type": "string",
                    "description": "10-digit Google Ads customer id, when known.",
                },
                "account_query": {
                    "type": "string",
                    "description": "Account name to search under the MCC, when customer_id is unknown.",
                },
                "campaign_id": {
                    "type": "string",
                    "description": "Campaign id to drill into (drill scopes).",
                },
                "campaign_name": {
                    "type": "string",
                    "description": (
                        "Campaign name to drill into — resolved against the "
                        "account's campaigns. Use this when you only know the "
                        "name the user said."
                    ),
                },
                "metric": {
                    "type": "string",
                    "enum": ["cost_per_conv", "roas"],
                    "description": (
                        "Headline metric for the table. Default cost_per_conv. "
                        "Pass 'roas' ONLY when the user explicitly asks about "
                        "ROAS / return on ad spend."
                    ),
                },
                "include_charts": {
                    "type": "boolean",
                    "description": (
                        "Default false. Set true ONLY when the user explicitly "
                        "asks for a chart or graph."
                    ),
                },
                "chart_metrics": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [
                            "cost",
                            "conversions",
                            "clicks",
                            "impressions",
                            "cost_per_conv",
                            "cpc",
                            "conv_rate",
                            "ctr",
                        ],
                    },
                    "description": "Metrics to chart when include_charts is true.",
                },
                "chart_days": {
                    "type": "integer",
                    "description": (
                        "Lookback in days for charts (e.g. 7, 30, 90). Default 30."
                    ),
                },
                "include_change": {
                    "type": "boolean",
                    "description": (
                        "Default false. Set true ONLY when the user asks for "
                        "percent-change / deltas IN THE TABLE."
                    ),
                },
                "extras": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["wow", "yoy", "day_of_week", "90d"],
                    },
                    "description": (
                        "Optional digest slices. Default empty. Add values "
                        "ONLY when explicitly asked: wow=week-over-week, "
                        "yoy=year-over-year/last year, day_of_week=weekday "
                        "patterns, 90d=90-day/quarter/long-term trend."
                    ),
                },
                "date_from": {
                    "type": "string",
                    "description": (
                        "Custom range start date (YYYY-MM-DD). Use together "
                        "with date_to when the user asks for a specific "
                        "calendar period: 'this month', 'May 1–20', etc. "
                        "Resolve the date from the TODAY field in context."
                    ),
                },
                "date_to": {
                    "type": "string",
                    "description": (
                        "Custom range end date (YYYY-MM-DD, inclusive). "
                        "Clamped to yesterday automatically. Use with date_from."
                    ),
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
    channel_id: str = "",
    thread_ts: str = "",
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
    if name == "fetch_google_ads_kpis":
        return await _fetch_google_ads_kpis(inputs, user_id=user_id)
    if name == "get_google_ads_data":
        return await _get_google_ads_data(
            inputs,
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
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
    busy: list[TimeSlot] | None = None,
) -> dict | None:
    """If [start, end] overlaps existing events, return a pending-action dict
    that proposes the next free slot of the same duration on/after the
    requested start. Returns None if no alternative could be found.
    """
    duration_min = max(int((end - start).total_seconds() // 60), 15)
    if busy is None:
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
    range_error = _validate_time_range(start, end)
    if range_error:
        return {"error": range_error}
    description = inputs.get("description", "")
    confirmed = bool(inputs.get("confirmed"))
    safe_title = escape_slack_text(title or "(untitled)")

    if not confirmed:
        when = fmt_local_range(start, end, tz_name)
        lines = [f"📅 Create *{safe_title}* — *{when}*"]

        # Conflict check
        horizon_events = await gcal.list_events(
            user_id, time_min=start - timedelta(hours=4), time_max=start + timedelta(days=5)
        )
        overlaps = gcal.find_overlaps_in_events(horizon_events, start, end)
        alternate: dict | None = None
        if overlaps:
            ov = overlaps[0]
            ov_when = _format_overlap_when(ov, tz_name)
            ov_title = escape_slack_text(ov.get("title") or "(untitled)")
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
                busy=gcal.busy_slots_from_events(horizon_events),
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
        "message": f"Created event {escape_slack_text(title)!r}.",
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
    current_is_all_day = gcal.event_is_all_day(ev)

    if new_start and new_end:
        range_error = _validate_time_range(
            new_start,
            new_end,
            max_hours=None if current_is_all_day else 12,
        )
        if range_error:
            return {"error": range_error}

    if not any([new_title, new_description, new_start, new_end]):
        return {
            "error": "Nothing to update — provide at least one of title, description, "
            "start_iso, or end_iso."
        }

    if new_start or new_end:
        try:
            effective_start = new_start or _parse_dt(cur_start_iso, tz_name)
            effective_end = new_end or _parse_dt(cur_end_iso, tz_name)
        except Exception as exc:
            return {"error": f"Could not parse existing event time: {exc}"}
        range_error = _validate_time_range(
            effective_start,
            effective_end,
            max_hours=None if current_is_all_day else 12,
        )
        if range_error:
            return {"error": range_error}

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

        lines: list[str] = [f"📅 {verb} *{escape_slack_text(cur_title)}*"]

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
            lines.append(f"Title → *{escape_slack_text(new_title)}*")
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
                horizon_events = await gcal.list_events(
                    user_id,
                    time_min=effective_start - timedelta(hours=4),
                    time_max=effective_start + timedelta(days=5),
                )
                overlaps = gcal.find_overlaps_in_events(
                    horizon_events,
                    effective_start,
                    effective_end,
                    exclude_event_id=event_id,
                )
                if overlaps:
                    ov = overlaps[0]
                    ov_when = _format_overlap_when(ov, tz_name)
                    ov_title = escape_slack_text(ov.get("title") or "(untitled)")
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
                        busy=gcal.busy_slots_from_events(horizon_events),
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
        "message": f"Updated event {escape_slack_text(cur_title)!r}.",
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
                "Change Wrike task "
                f"“{escape_slack_text(task.get('title') or '(untitled)')}” "
                f"to status “{escape_slack_text(new_name)}”."
            ),
            "next_action": (
                "If the user approves, call update_wrike_task_status again with "
                "confirmed=true."
            ),
            "_resolved_task_id": task.get("id"),
        }

    await client.update_task_status(task["id"], custom_status_id=target_id)
    return {
        "ok": True,
        "message": (
            f"Updated {escape_slack_text(task.get('title') or '(untitled)')!r} "
            f"→ {escape_slack_text(new_name)}."
        ),
    }


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
                "Post this comment on "
                f"“{escape_slack_text(task.get('title') or '(untitled)')}”:\n"
                f"{quote_slack_text(text)}"
            ),
            "next_action": (
                "If the user approves, call post_wrike_task_comment again with the "
                "same text and confirmed=true."
            ),
            "_resolved_task_id": task.get("id"),
        }

    await client.post_task_comment(task["id"], text=text)
    return {
        "ok": True,
        "message": f"Comment posted on {escape_slack_text(task.get('title') or '(untitled)')!r}.",
    }


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


# ── Google Ads KPI reporting ───────────────────────────────────────────────


async def _fetch_google_ads_kpis(inputs: dict, *, user_id: int) -> dict:
    if not bool(inputs.get("confirmed")):
        return {"error": "Google Ads KPI pulls must be approved first."}
    customer_id = google_ads.normalize_customer_id(inputs.get("customer_id"))
    if not customer_id:
        return {"error": "Missing Google Ads customer_id."}
    mode = inputs.get("mode") if inputs.get("mode") in {"cpa", "roas"} else "cpa"
    report = await google_ads.build_kpi_report(
        user_id=user_id,
        customer_id=customer_id,
        mode=mode,
        frozen_windows=inputs.get("windows"),
    )
    # KPI rows + deltas are deterministic. The per-campaign notes are
    # model-written but constrained: explain_kpi_report hands the model a
    # fully-precomputed fact pack and drops any note whose figures don't
    # reconcile against it — so a bad note degrades to the plain rows.
    explanations = await explain_kpi_report(report)
    blocks = kpi_blocks.report_blocks(report, explanations)
    account = report.get("account") or {}
    return {
        "ok": True,
        "message": f"KPI report for {escape_slack_text(account.get('name'))}.",
        "settled_label": "Pulled",
        "blocks": blocks,
    }


# ── Google Ads analyst (read-only data) ────────────────────────────────────


_DRILL_SCOPES = {
    "campaign_detail",
    "keywords",
    "search_terms",
    "ad_groups",
    "ads",
    "products",
    "asset_groups",
}

_ADS_EXTRA_SLICES = {"wow", "yoy", "day_of_week", "90d"}


def _parse_date_arg(val: Any) -> date | None:
    """Parse a YYYY-MM-DD string from tool input. Returns None on failure."""
    if not val:
        return None
    try:
        return date.fromisoformat(str(val).strip())
    except ValueError:
        return None


async def _resolve_ads_account(
    client: google_ads.GoogleAdsClient, *, customer_id: str, account_query: str
):
    """Resolve to a GoogleAdsAccount, or return a dict the agent relays
    (an error, or a needs_disambiguation list)."""
    if customer_id:
        account = await client.account_by_id(customer_id)
        if account is None:
            return {
                "error": (
                    f"Customer id {customer_id} isn't an enabled account "
                    "under the configured MCC."
                ),
                "next_action": "Ask the user which Google Ads account they mean.",
            }
        return account
    if account_query:
        try:
            matches = await client.search_accounts(account_query)
        except Exception as exc:
            return {"error": f"Google Ads account search failed: {exc}"}
        if not matches:
            return {
                "error": (
                    "No enabled Google Ads account under the configured MCC "
                    f"matched '{account_query}'."
                )
            }
        if len(matches) > 1:
            return {
                "needs_disambiguation": True,
                "candidates": [
                    {"customer_id": m.customer_id, "name": m.name}
                    for m in matches[:10]
                ],
                "next_action": (
                    "List these accounts as a numbered list and ask which "
                    "one, then call get_google_ads_data again with that "
                    "customer_id."
                ),
            }
        return matches[0]
    return {
        "error": "No account specified.",
        "next_action": "Ask the user which Google Ads account they mean.",
    }


_CHART_METRIC_LABELS = {
    "cost": "Cost",
    "conversions": "Conversions",
    "clicks": "Clicks",
    "impressions": "Impressions",
    "cost_per_conv": "Cost/conv",
    "cpc": "CPC",
    "conv_rate": "Conv rate",
    "ctr": "CTR",
}


def _daily_metric_value(day: dict, metric: str) -> float | None:
    """Derive one metric for a single day of the trend series."""
    cost = day.get("cost") or 0
    conv = day.get("conversions") or 0
    clicks = day.get("clicks") or 0
    impr = day.get("impressions") or 0
    if metric == "cost":
        return cost
    if metric == "conversions":
        return conv
    if metric == "clicks":
        return clicks
    if metric == "impressions":
        return impr
    if metric == "cost_per_conv":
        return round(cost / conv, 2) if conv else None
    if metric == "cpc":
        return round(cost / clicks, 2) if clicks else None
    if metric == "conv_rate":
        return round(conv / clicks, 4) if clicks else None
    if metric == "ctr":
        return round(clicks / impr, 4) if impr else None
    return None


def _overview_chart_specs(
    snapshot: dict, chart_metrics: list[str], chart_days: int
) -> list[dict]:
    """Line charts of the requested daily metrics over the last N days. Built
    only when the user explicitly asked for a chart."""
    trend = snapshot.get("daily_trend_90d") or []
    if not trend:
        return []
    window = trend[-chart_days:] if chart_days > 0 else trend
    x = [str(d.get("date") or "")[5:] for d in window]
    metrics = [m for m in chart_metrics if m in _CHART_METRIC_LABELS] or ["cost"]
    specs: list[dict] = []
    for metric in metrics:
        label = _CHART_METRIC_LABELS[metric]
        specs.append(
            {
                "kind": "line",
                "title": f"{label} — last {len(window)} days",
                "filename": f"{metric}_trend.png",
                "x": x,
                "y": [_daily_metric_value(d, metric) for d in window],
                "y_label": label,
            }
        )
    return specs


def _campaign_chart_specs(
    snapshot: dict, campaign: dict, chart_metrics: list[str], chart_days: int
) -> list[dict]:
    """Line charts of ONE campaign's daily metrics over the last N days."""
    series = (snapshot.get("daily_by_campaign") or {}).get(
        str(campaign.get("campaign_id"))
    ) or []
    if not series:
        return []
    window = series[-chart_days:] if chart_days > 0 else series
    x = [str(d.get("date") or "")[5:] for d in window]
    metrics = [m for m in chart_metrics if m in _CHART_METRIC_LABELS] or ["cost"]
    name = campaign.get("name") or "Campaign"
    specs: list[dict] = []
    for metric in metrics:
        label = _CHART_METRIC_LABELS[metric]
        specs.append(
            {
                "kind": "line",
                "title": f"{name} — {label}, last {len(window)} days",
                "filename": f"campaign_{metric}_trend.png",
                "x": x,
                "y": [_daily_metric_value(d, metric) for d in window],
                "y_label": label,
            }
        )
    return specs


def _row_metric_value(row: dict, metric: str) -> Any:
    """Read an already-derived metric off a drill row (`cpa` is cost_per_conv)."""
    return row.get("cpa") if metric == "cost_per_conv" else row.get(metric)


_BAR_VALUE_KINDS = {
    "cost": "money",
    "cpc": "money",
    "cost_per_conv": "money",
    "conv_rate": "percent",
    "ctr": "percent",
}


def _drill_chart_specs(drill: dict, chart_metrics: list[str]) -> list[dict]:
    """A bar of the first drill dimension, ranked by the requested metric."""
    account = drill.get("account") or {}
    currency = ads_blocks.currency_symbol(account.get("currency"))
    metric = next(
        (m for m in chart_metrics if m in _CHART_METRIC_LABELS), "cost"
    )
    value_kind = _BAR_VALUE_KINDS.get(metric, "number")
    for dimension, payload in (drill.get("breakdown") or {}).items():
        rows = payload.get("rows") or []
        if not rows:
            continue
        top = sorted(
            rows, key=lambda r: -(_row_metric_value(r, metric) or 0)
        )[:10]
        return [
            {
                "kind": "bar",
                "title": (
                    f"Top {dimension.replace('_', ' ')} by "
                    f"{_CHART_METRIC_LABELS[metric].lower()}"
                ),
                "filename": f"{dimension}_{metric}.png",
                "labels": [r.get("label") or "" for r in top],
                "values": [_row_metric_value(r, metric) or 0 for r in top],
                "value_kind": value_kind,
                "currency": currency if value_kind == "money" else "",
            }
        ]
    return []


# Cap concurrent matplotlib renders — each figure is a transient memory spike.
_CHART_SEMAPHORE = asyncio.Semaphore(2)


async def _render_specs(specs: list[dict]) -> list[dict]:
    """Render chart PNGs off the event loop (matplotlib is blocking CPU work),
    capped so concurrent renders can't spike memory."""
    if not specs:
        return []
    try:
        async with _CHART_SEMAPHORE:
            return await asyncio.to_thread(ads_charts.render_charts, specs)
    except Exception as exc:
        logger.warning(f"Google Ads chart render failed: {exc}")
        return []


# ── Slim, labeled fact pack for the model ──────────────────────────────────
#
# The full snapshot / drill drives the deterministic tables + charts. The model
# only NARRATES, so it gets a trimmed, labeled digest — no 90-day daily series,
# no unused per-campaign windows, no positional column counting.


def _slim_metrics(m: dict, use_roas: bool) -> dict:
    """Keep the metrics the narration uses; drop conversion value (and ROAS
    unless the user asked for it). `cpa` is surfaced as `cost_per_conv`."""
    if not m:
        return {}
    out = {
        k: m.get(k)
        for k in (
            "cost",
            "conversions",
            "clicks",
            "impressions",
            "cpc",
            "conv_rate",
            "ctr",
        )
    }
    out["cost_per_conv"] = m.get("cpa")
    if use_roas:
        out["roas"] = m.get("roas")
    return out


def _slim_deltas(d: dict, use_roas: bool) -> dict:
    """Keep only deltas the narration is allowed to use."""
    if not d:
        return {}
    out = {
        "cost_pct": d.get("cost_pct"),
        "conversions_pct": d.get("conversions_pct"),
        "clicks_pct": d.get("clicks_pct"),
        "impressions_pct": d.get("impressions_pct"),
        "cpc_pct": d.get("cpc_pct"),
        "conv_rate_pct": d.get("conv_rate_pct"),
        "ctr_pct": d.get("ctr_pct"),
    }
    if use_roas:
        out["roas_pct"] = d.get("roas_pct")
    else:
        out["cost_per_conv_pct"] = d.get("cpa_pct")
    return out


def _campaign_digest_row(c: dict, use_roas: bool) -> dict:
    m = c.get("last_30") or {}
    d = c.get("delta_30d_vs_prior_30") or {}
    budget = c.get("budget") or {}
    share = c.get("impression_share") or {}
    row = {
        "name": c.get("name"),
        "channel": c.get("channel"),
        "status": c.get("status"),
        "cost": m.get("cost"),
        "conversions": m.get("conversions"),
        "clicks": m.get("clicks"),
        "impressions": m.get("impressions"),
        "cpc": m.get("cpc"),
        "conv_rate": m.get("conv_rate"),
        "ctr": m.get("ctr"),
        **_slim_deltas(d, use_roas),
        "budget_pacing_pct": budget.get("pacing_pct"),
        "budget_limited": budget.get("budget_limited"),
        "lost_is_budget": share.get("lost_is_budget"),
        "lost_is_rank": share.get("lost_is_rank"),
    }
    if use_roas:
        row["roas"] = m.get("roas")
    else:
        row["cost_per_conv"] = m.get("cpa")
    return row


def _daily_metric_series(days: list[dict], metric: str) -> list[float | None]:
    return [_daily_metric_value(day, metric) for day in days]


_PERIOD_VALUE_BASIS = {
    "cost": "average_daily_value",
    "conversions": "average_daily_value",
    "clicks": "average_daily_value",
    "impressions": "average_daily_value",
    "cost_per_conv": "weighted_by_conversions",
    "cpc": "weighted_by_clicks",
    "conv_rate": "weighted_by_clicks",
    "ctr": "weighted_by_impressions",
    "roas": "weighted_by_cost",
}


def _period_metric_value(days: list[dict], metric: str) -> float | None:
    """One period-level metric value.

    Volume metrics are average-daily values so the earlier/recent halves are
    comparable when the split has an odd day count. Ratio metrics are weighted
    from summed denominators, never averaged from daily ratios.
    """
    if not days:
        return None

    totals = google_ads_snapshot.zero_metrics()
    for day in days:
        google_ads_snapshot.accumulate(totals, day)

    if metric in {"cost", "conversions", "clicks", "impressions"}:
        return round((totals.get(metric) or 0) / len(days), 4)

    derived = google_ads_snapshot.derive_metrics(totals)
    if metric == "cost_per_conv":
        return derived.get("cpa")
    return derived.get(metric)


def _campaign_trend_summary(
    snapshot: dict, campaign: dict, metric: str, chart_days: int
) -> dict | None:
    """Small deterministic trend summary for one campaign chart request.

    The raw daily series stays tool-internal; the model gets only enough
    labeled facts to narrate direction without inventing from the chart.
    """
    series = (snapshot.get("daily_by_campaign") or {}).get(
        str(campaign.get("campaign_id"))
    ) or []
    if not series:
        return None
    window = series[-chart_days:] if chart_days > 0 else series
    daily_values = _daily_metric_series(window, metric)
    valid = [v for v in daily_values if v is not None]
    if not valid:
        return None

    midpoint = max(1, len(window) // 2)
    earlier_value = _period_metric_value(window[:midpoint], metric)
    recent_value = _period_metric_value(window[midpoint:], metric)
    change_pct = google_ads_snapshot.pct_change(recent_value, earlier_value)
    if change_pct is None:
        direction = "flat"
    elif change_pct > 2:
        direction = "up"
    elif change_pct < -2:
        direction = "down"
    else:
        direction = "flat"

    return {
        "campaign_name": campaign.get("name"),
        "metric": metric,
        "days": len(window),
        "start": window[0].get("date"),
        "end": window[-1].get("date"),
        "first_daily_value": daily_values[0],
        "last_daily_value": daily_values[-1],
        "min_daily_value": min(valid),
        "max_daily_value": max(valid),
        "earlier_period_value": earlier_value,
        "recent_period_value": recent_value,
        "period_value_basis": _PERIOD_VALUE_BASIS.get(metric, "period_value"),
        "recent_vs_earlier_pct": change_pct,
        "direction": direction,
    }


def _daily_trend_summary(
    days: list[dict], metric: str, *, max_days: int = 90
) -> dict | None:
    """Deterministic summary of a raw daily trend, without sending raw rows."""
    window = days[-max_days:] if max_days > 0 else days
    if not window:
        return None
    daily_values = _daily_metric_series(window, metric)
    valid = [v for v in daily_values if v is not None]
    if not valid:
        return None

    midpoint = max(1, len(window) // 2)
    earlier_value = _period_metric_value(window[:midpoint], metric)
    recent_value = _period_metric_value(window[midpoint:], metric)
    change_pct = google_ads_snapshot.pct_change(recent_value, earlier_value)
    if change_pct is None:
        direction = "flat"
    elif change_pct > 2:
        direction = "up"
    elif change_pct < -2:
        direction = "down"
    else:
        direction = "flat"

    return {
        "metric": metric,
        "days": len(window),
        "start": window[0].get("date"),
        "end": window[-1].get("date"),
        "first_daily_value": daily_values[0],
        "last_daily_value": daily_values[-1],
        "min_daily_value": min(valid),
        "max_daily_value": max(valid),
        "earlier_period_value": earlier_value,
        "recent_period_value": recent_value,
        "period_value_basis": _PERIOD_VALUE_BASIS.get(metric, "period_value"),
        "recent_vs_earlier_pct": change_pct,
        "direction": direction,
    }


def _account_90d_summary(snapshot: dict, use_roas: bool) -> dict:
    trend = snapshot.get("daily_trend_90d") or []
    return _multi_metric_90d_summary(trend, use_roas)


def _campaign_90d_summary(snapshot: dict, campaign: dict, use_roas: bool) -> dict:
    trend = (snapshot.get("daily_by_campaign") or {}).get(
        str(campaign.get("campaign_id"))
    ) or []
    return _multi_metric_90d_summary(trend, use_roas)


def _multi_metric_90d_summary(days: list[dict], use_roas: bool) -> dict:
    metrics = ["cost", "conversions", "cpc", "conv_rate", "ctr"]
    if not use_roas:
        metrics.insert(2, "cost_per_conv")
    return {
        metric: summary
        for metric in metrics
        if (summary := _daily_trend_summary(days, metric)) is not None
    }


def _add_digest_extras(
    out: dict, snapshot: dict, *, extras: set[str], use_roas: bool
) -> None:
    totals = snapshot.get("account_totals") or {}
    deltas = snapshot.get("account_deltas") or {}
    if "wow" in extras:
        out.setdefault("account_totals", {})["last_7"] = _slim_metrics(
            totals.get("last_7") or {}, use_roas
        )
        out.setdefault("account_deltas", {})["wow_7d"] = _slim_deltas(
            deltas.get("wow_7d") or {}, use_roas
        )
    if "yoy" in extras:
        out.setdefault("account_deltas", {})["yoy_30d"] = _slim_deltas(
            deltas.get("yoy_30d") or {}, use_roas
        )
    if "day_of_week" in extras:
        out["day_of_week_avg"] = snapshot.get("day_of_week_avg")
    if "90d" in extras:
        out["trend_90d"] = _account_90d_summary(snapshot, use_roas)


def _add_campaign_extras(
    out: dict,
    snapshot: dict,
    campaign: dict,
    *,
    extras: set[str],
    use_roas: bool,
) -> None:
    c = out.setdefault("campaign", {})
    if "wow" in extras:
        last_7 = campaign.get("last_7") or {}
        prior_7 = campaign.get("prior_7") or {}
        c["last_7"] = _slim_metrics(last_7, use_roas)
        c["delta_7d_vs_prior_7"] = _slim_deltas(
            google_ads_snapshot.deltas(last_7, prior_7), use_roas
        )
    if "yoy" in extras:
        c["delta_30d_vs_yoy"] = _slim_deltas(
            google_ads_snapshot.deltas(
                campaign.get("last_30") or {},
                campaign.get("yoy_last_30") or {},
            ),
            use_roas,
        )
    if "day_of_week" in extras:
        series = (snapshot.get("daily_by_campaign") or {}).get(
            str(campaign.get("campaign_id"))
        ) or []
        c["day_of_week_avg"] = google_ads_snapshot.day_of_week_summary(series)
    if "90d" in extras:
        c["trend_90d"] = _campaign_90d_summary(snapshot, campaign, use_roas)


def _custom_range_digest(snapshot: dict, metric: str) -> dict:
    """Slim model-facing fact pack for a custom date range.

    No period-over-period deltas — only the absolute metrics for the window.
    """
    use_roas = metric == "roas"
    account = snapshot.get("account") or {}
    date_range = snapshot.get("date_range") or {}
    totals = snapshot.get("account_totals") or {}

    campaigns = [
        _campaign_digest_row(c, use_roas)
        for c in (snapshot.get("campaigns") or [])[:8]
    ]

    by_channel = {
        channel: {
            "campaign_count": agg.get("campaign_count"),
            "period": _slim_metrics(agg.get("last_30") or {}, use_roas),
        }
        for channel, agg in (snapshot.get("by_channel") or {}).items()
    }

    return {
        "account": {
            "name": account.get("name"),
            "customer_id": account.get("customer_id"),
            "currency": account.get("currency"),
        },
        "period": {
            "label": "custom range",
            "start": date_range.get("start"),
            "end": date_range.get("end"),
            "days": date_range.get("days"),
            "as_of": snapshot.get("as_of"),
        },
        "kind": "custom_range",
        "account_totals": {
            "period": _slim_metrics(totals.get("last_30") or {}, use_roas),
        },
        "by_channel": by_channel,
        "campaign_count": snapshot.get("campaign_count"),
        "campaigns": campaigns,
        "other_campaigns": snapshot.get("other_campaigns"),
        "note": "Custom date range — no period-over-period comparison available.",
    }


def _overview_digest(
    snapshot: dict,
    metric: str,
    *,
    selected_campaign: dict | None = None,
    trend_metric: str | None = None,
    chart_days: int = 30,
    extras: set[str] | None = None,
) -> dict:
    """Slim, model-facing view of an account snapshot."""
    use_roas = metric == "roas"
    account = snapshot.get("account") or {}
    window = (snapshot.get("windows") or {}).get("last_30") or {}
    totals = snapshot.get("account_totals") or {}
    requested_extras = extras or set()

    base = {
        "account": {
            "name": account.get("name"),
            "customer_id": account.get("customer_id"),
            "currency": account.get("currency"),
        },
        "period": {
            "label": "last 30 days",
            "start": window.get("start"),
            "end": window.get("end"),
            "as_of": snapshot.get("as_of"),
        },
    }

    if selected_campaign:
        out = {
            **base,
            "kind": "selected_campaign",
            "campaign": _campaign_digest_row(selected_campaign, use_roas),
        }
        if trend_metric:
            out["campaign_trend"] = _campaign_trend_summary(
                snapshot, selected_campaign, trend_metric, chart_days
            )
        _add_campaign_extras(
            out,
            snapshot,
            selected_campaign,
            extras=requested_extras,
            use_roas=use_roas,
        )
        return out

    source_campaigns = list(snapshot.get("campaigns") or [])
    digest_campaigns = source_campaigns[:8]

    campaigns = [_campaign_digest_row(c, use_roas) for c in digest_campaigns]

    by_channel = {
        channel: {
            "campaign_count": agg.get("campaign_count"),
            "last_30": _slim_metrics(agg.get("last_30") or {}, use_roas),
            "delta_30d_vs_prior_30": _slim_deltas(
                agg.get("delta_30d_vs_prior_30") or {}, use_roas
            ),
        }
        for channel, agg in (snapshot.get("by_channel") or {}).items()
    }

    out = {
        **base,
        "kind": "account_overview",
        "account_totals": {"last_30": _slim_metrics(totals.get("last_30") or {}, use_roas)},
        "account_deltas": {
            "mom_30d": _slim_deltas(
                (snapshot.get("account_deltas") or {}).get("mom_30d") or {},
                use_roas,
            )
        },
        "by_channel": by_channel,
        "campaign_count": snapshot.get("campaign_count"),
        "campaigns": campaigns,
        "other_campaigns": snapshot.get("other_campaigns"),
    }
    if trend_metric:
        out["account_trend"] = _daily_trend_summary(
            snapshot.get("daily_trend_90d") or [],
            trend_metric,
            max_days=chart_days,
        )
    _add_digest_extras(out, snapshot, extras=requested_extras, use_roas=use_roas)
    return out


def _drill_digest(drill: dict, metric: str) -> dict:
    """Slim, model-facing view of a campaign drill-down."""
    use_roas = metric == "roas"
    account = drill.get("account") or {}
    campaign = drill.get("campaign") or {}

    breakdown: dict[str, Any] = {}
    for dim, payload in (drill.get("breakdown") or {}).items():
        if payload.get("error"):
            breakdown[dim] = {"error": payload["error"]}
            continue
        rows = []
        for r in (payload.get("rows") or [])[:15]:
            row = {
                "label": r.get("label"),
                "cost": r.get("cost"),
                "conversions": r.get("conversions"),
                "cpc": r.get("cpc"),
                "conv_rate": r.get("conv_rate"),
                "ctr": r.get("ctr"),
            }
            if use_roas:
                row["roas"] = r.get("roas")
            else:
                row["cost_per_conv"] = r.get("cpa")
            rows.append(row)
        breakdown[dim] = rows

    return {
        "account": {"name": account.get("name"), "currency": account.get("currency")},
        "campaign": {
            "name": campaign.get("name"),
            "channel": campaign.get("channel"),
            "status": campaign.get("status"),
            "last_30": _slim_metrics(campaign.get("last_30") or {}, use_roas),
            "delta_30d_vs_prior_30": campaign.get("delta_30d_vs_prior_30"),
        },
        "window": drill.get("window"),
        "breakdown": breakdown,
    }


async def _get_google_ads_data(
    inputs: dict, *, user_id: int, channel_id: str, thread_ts: str
) -> dict:
    """Read-only Google Ads data pull. scope='account_overview' returns the
    surface snapshot; the drill scopes return a channel-aware campaign
    breakdown. Returns the fact pack for the agent to narrate plus ready-made
    table/chart output for the caller to post in-thread.
    """
    scope = (inputs.get("scope") or "account_overview").strip()
    customer_id = google_ads.normalize_customer_id(inputs.get("customer_id"))
    account_query = (inputs.get("account_query") or "").strip()
    campaign_id = str(inputs.get("campaign_id") or "").strip()
    campaign_name = (inputs.get("campaign_name") or "").strip()
    metric = "roas" if inputs.get("metric") == "roas" else "cost_per_conv"
    include_charts = bool(inputs.get("include_charts"))
    include_change = bool(inputs.get("include_change"))
    raw_extras = inputs.get("extras")
    extras = {
        str(item)
        for item in raw_extras
        if str(item) in _ADS_EXTRA_SLICES
    } if isinstance(raw_extras, list) else set()
    chart_metrics = inputs.get("chart_metrics")
    if not isinstance(chart_metrics, list):
        chart_metrics = []
    try:
        chart_days = max(1, min(int(inputs.get("chart_days") or 30), 90))
    except (TypeError, ValueError):
        chart_days = 30

    date_from = _parse_date_arg(inputs.get("date_from"))
    date_to = _parse_date_arg(inputs.get("date_to"))

    # A campaign reference no longer forces a drill — only an explicit drill
    # scope does. A metric / trend question about a campaign is answered from
    # the account_overview digest (which carries every campaign's deltas).
    is_drill = scope in _DRILL_SCOPES

    try:
        client = await google_ads.GoogleAdsClient.for_user(user_id)
    except google_ads.GoogleAdsNotConnectedError:
        return {"error": "Google Ads isn't connected. Run /connect to link it."}
    except google_ads.GoogleAdsConfigError as exc:
        return {"error": f"Google Ads isn't configured: {exc}"}

    resolved = await _resolve_ads_account(
        client, customer_id=customer_id, account_query=account_query
    )
    if isinstance(resolved, dict):
        return resolved  # error or needs_disambiguation — the agent relays it
    account = resolved

    # ── Custom date range (account_overview only) ──────────────────────────
    if date_from is not None and date_to is not None and not is_drill:
        yesterday = date.today() - timedelta(days=1)
        date_to = min(date_to, yesterday)
        if date_from > date_to:
            return {
                "error": (
                    f"date_from ({date_from}) must be on or before date_to "
                    f"({date_to})."
                )
            }
        if (date_to - date_from).days > 364:
            return {"error": "Custom date ranges are limited to 365 days."}

        custom_window = google_ads.DateWindow(
            "custom", date_from, date_to
        )
        custom_key = (
            user_id, channel_id, thread_ts, account.customer_id,
            date_from.isoformat(), date_to.isoformat(),
        )
        try:
            custom_snap = await google_ads_snapshot.get_or_build_custom_range(
                user_id=user_id,
                account=account,
                window=custom_window,
                cache_key=custom_key,
            )
        except google_ads.GoogleAdsNotConnectedError:
            return {"error": "Google Ads isn't connected. Run /connect to link it."}
        except Exception as exc:
            logger.exception("Google Ads custom range fetch failed")
            return {"error": f"Couldn't pull Google Ads data for that date range: {exc}"}

        blocks = ads_blocks.account_overview_card(
            custom_snap,
            metric=metric,
            include_change=False,
            period_label="",
        )
        return {
            "ok": True,
            "kind": "custom_range",
            "message": f"Google Ads — {account.name} ({date_from} to {date_to}).",
            "fact_pack": _custom_range_digest(custom_snap, metric),
            "blocks": blocks,
            "images": [],
            "_resolved_customer_id": account.customer_id,
            "_resolved_account_name": account.name,
        }

    snap_key = (user_id, channel_id, thread_ts, account.customer_id)
    try:
        snapshot = await google_ads_snapshot.get_or_build_snapshot(
            user_id=user_id, account=account, cache_key=snap_key
        )
    except google_ads.GoogleAdsNotConnectedError:
        return {"error": "Google Ads isn't connected. Run /connect to link it."}
    except Exception as exc:
        logger.exception("Google Ads snapshot fetch failed")
        return {"error": f"Couldn't pull Google Ads data: {exc}"}

    if not is_drill:
        images: list[dict] = []
        selected_campaign: dict | None = None
        chart_metric = next(
            (m for m in chart_metrics if m in _CHART_METRIC_LABELS), None
        )
        if campaign_id or campaign_name:
            resolved = google_ads_drill.resolve_campaign(
                snapshot,
                campaign_id=campaign_id or None,
                campaign_name=campaign_name or None,
            )
            if isinstance(resolved, dict):
                selected_campaign = resolved
            elif isinstance(resolved, list):
                return {
                    "needs_disambiguation": True,
                    "candidates": [{"name": c["name"]} for c in resolved[:10]],
                    "next_action": (
                        "List these campaigns and ask which one, then call "
                        "get_google_ads_data again with that campaign_name."
                    ),
                }
            else:
                return {
                    "error": f"I couldn't find that campaign in {account.name}.",
                    "next_action": (
                        "Ask which campaign name they mean, then call "
                        "get_google_ads_data again with that campaign_name."
                    ),
                }
        if include_charts:
            specs = (
                _campaign_chart_specs(
                    snapshot, selected_campaign, chart_metrics, chart_days
                )
                if selected_campaign
                else _overview_chart_specs(snapshot, chart_metrics, chart_days)
            )
            if selected_campaign and not specs:
                return {
                    "error": (
                        f"I couldn't build a daily chart for "
                        f"{selected_campaign.get('name') or 'that campaign'}."
                    )
                }
            images = await _render_specs(specs)
        blocks = (
            None
            if selected_campaign or include_charts
            else ads_blocks.account_overview_card(
                snapshot, metric=metric, include_change=include_change
            )
        )
        return {
            "ok": True,
            "kind": (
                "campaign_chart"
                if include_charts and selected_campaign
                else "campaign_metric"
                if selected_campaign
                else "account_overview"
            ),
            "message": (
                f"Google Ads chart — {selected_campaign.get('name')}."
                if include_charts and selected_campaign
                else f"Google Ads campaign — {selected_campaign.get('name')}."
                if selected_campaign
                else f"Google Ads overview — {account.name}."
            ),
            "fact_pack": _overview_digest(
                snapshot,
                metric,
                selected_campaign=selected_campaign,
                trend_metric=chart_metric,
                chart_days=chart_days,
                extras=extras,
            ),
            "blocks": blocks,
            "images": images,
            "_resolved_customer_id": account.customer_id,
            "_resolved_account_name": account.name,
            "_resolved_campaign_name": (
                selected_campaign.get("name") if selected_campaign else None
            ),
        }

    # Drill into one campaign.
    campaign = google_ads_drill.resolve_campaign(
        snapshot,
        campaign_id=campaign_id or None,
        campaign_name=campaign_name or None,
    )
    if campaign is None:
        return {
            "error": f"I couldn't find that campaign in {account.name}.",
            "next_action": (
                "Ask which campaign, or call get_google_ads_data with "
                "scope='account_overview' to list them."
            ),
        }
    if isinstance(campaign, list):
        return {
            "needs_disambiguation": True,
            "candidates": [
                {"campaign_id": c["campaign_id"], "name": c["name"]}
                for c in campaign[:10]
            ],
            "next_action": (
                "List these campaigns and ask which one, then call "
                "get_google_ads_data again with that campaign_id."
            ),
        }

    dimensions, scope_note = google_ads_drill.dimensions_for(
        campaign.get("channel"), scope
    )
    drill_key = (
        user_id,
        channel_id,
        thread_ts,
        account.customer_id,
        campaign["campaign_id"],
        tuple(sorted(dimensions)),
    )
    try:
        drill = await google_ads_drill.get_or_build_drill(
            client=client,
            account=account,
            campaign=campaign,
            dimensions=dimensions,
            cache_key=drill_key,
        )
    except Exception as exc:
        logger.exception("Google Ads drill fetch failed")
        return {"error": f"Couldn't pull the campaign breakdown: {exc}"}

    result: dict = {
        "ok": True,
        "kind": "campaign_detail",
        "message": f"Google Ads drill-down — {campaign.get('name')}.",
        "fact_pack": _drill_digest(drill, metric),
        "blocks": ads_blocks.campaign_detail_card(drill, metric=metric),
        "images": (
            await _render_specs(_drill_chart_specs(drill, chart_metrics))
            if include_charts
            else []
        ),
        "_resolved_customer_id": account.customer_id,
        "_resolved_account_name": account.name,
    }
    if scope_note:
        result["scope_note"] = scope_note
    return result


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
    range_error = _validate_time_range(start, end)
    if range_error:
        return {"error": range_error}

    if not confirmed:
        return {
            "preview": True,
            "summary_for_user": (
                f"Schedule *{escape_slack_text(title)}* — "
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
        "message": (
            f"Scheduled *{escape_slack_text(title)}* and marked as "
            f"{SCHEDULED_STATUS_NAME}.{escape_slack_text(status_msg)}"
        ),
    }


# ── User settings ──────────────────────────────────────────────────────────


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
            summary = f"Save new notes:\n{quote_slack_text(preview)}"
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


def _validate_time_range(
    start: datetime, end: datetime, *, max_hours: int | None = 12
) -> str | None:
    if end <= start:
        return "End time must be after start time."
    if max_hours is not None and (end - start) > timedelta(hours=max_hours):
        return f"Calendar blocks longer than {max_hours} hours are not supported here."
    return None


def _format_overlap_when(ov: dict, tz_name: str) -> str:
    """Format an overlap event's time range for inline display in a preview."""
    try:
        s = _parse_dt(ov["start"], tz_name)
        e = _parse_dt(ov["end"], tz_name)
    except Exception:
        return ""
    # Drop the date prefix — the surrounding line already establishes "this day".
    return f"{fmt_local_time(s, tz_name)}–{fmt_local_time(e, tz_name)}"
