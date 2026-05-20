"""/wrike — list New tasks, then drive a multi-turn flow to plot one onto the calendar."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from app.config import settings
from app.db.engine import session_scope
from app.db.users import get_or_create_user
from app.formatters import wrike_blocks as wb
from app.integrations import google_calendar as gcal
from app.integrations import wrike as wrike_int
from app.llm.extract_schedule import extract_schedule_intent
from openinference.semconv.trace import OpenInferenceSpanKindValues as Kind
from openinference.semconv.trace import SpanAttributes

from app.observability import start_span
from app.sessions import store as session_store
from app.slack_app.approval import build_approval_blocks_with_alternates
from app.slack_app.connect_prompt import connect_prompt_blocks
from app.utils.slack_mrkdwn import escape_slack_text
from app.utils.timezone import end_of_day, now_in, start_of_day, user_tz
from app.utils.working_hours import (
    TimeSlot,
    find_free_slots,
    first_free_slot_for_duration,
    working_window,
)

SESSION_NAME = "wrike"
COMMAND_TRIGGER = "/wrike"


# ── Step 1: /wrike — list tasks + slots, save state ────────────────────────


async def _list_tasks_for_user(user_id: int) -> list[dict]:
    client = await wrike_int.WrikeClient.for_user(user_id)
    me = await client.me()
    me_id = me.get("id")
    if not me_id:
        return []
    # Resolve every "New" status across every workflow.
    new_status_ids = await wrike_int.resolve_status_ids(user_id, "New")
    # Wrike rejects `customStatuses`, `dueDate`, `permalink` in the `fields` query;
    # status & dates come back by default; permalink is reconstructed in task_permalink().
    fields = ["responsibleIds", "description"]
    tasks = await client.tasks(
        responsibles=[me_id],
        custom_statuses=new_status_ids if new_status_ids else None,
        status="Active" if not new_status_ids else None,
        fields=fields,
    )
    return [
        {
            "id": t.get("id"),
            "title": t.get("title"),
            "due_date": (t.get("dates") or {}).get("due"),
            "permalink": wrike_int.task_permalink(t),
            "description": t.get("description"),
        }
        for t in tasks
    ]


async def _slots_for_horizon(
    *,
    user_id: int,
    tz_name: str,
    workday_start: str,
    workday_end: str,
    horizon_days: int = 3,
) -> list[tuple[datetime, list[TimeSlot]]]:
    today = now_in(tz_name)
    horizon_end = end_of_day(today + timedelta(days=horizon_days - 1), tz_name)
    events = await gcal.list_events(
        user_id, time_min=start_of_day(today, tz_name), time_max=horizon_end
    )
    busy_all = [s for s in (gcal.event_to_busy_slot(ev) for ev in events) if s]

    out: list[tuple[datetime, list[TimeSlot]]] = []
    for offset in range(horizon_days):
        day = today + timedelta(days=offset)
        ww = working_window(day, workday_start, workday_end, tz_name)
        # On day 0, clip start to "now" so we don't suggest already-passed slots.
        if offset == 0 and ww.start < today:
            ww = TimeSlot(start=today, end=ww.end)
        if ww.end <= ww.start:
            continue
        slots = find_free_slots(ww, busy_all, min_minutes=30)
        if slots:
            out.append((day, slots))
    return out


def register(app):
    @app.command("/wrike")
    async def handle(ack, body, client):
        await ack()
        slack_team_id = body["team_id"]
        slack_user_id = body["user_id"]

        user_info = await client.users_info(user=slack_user_id)
        u = user_info.get("user", {})
        tz_name = u.get("tz") or "UTC"
        real_name = u.get("real_name") or u.get("name") or "there"
        email = (u.get("profile") or {}).get("email")

        async with session_scope() as session:
            user = await get_or_create_user(
                session,
                slack_team_id=slack_team_id,
                slack_user_id=slack_user_id,
                email=email,
                real_name=real_name,
                tz=tz_name,
            )
            user_id = user.id
            workday_start = user.workday_start
            workday_end = user.workday_end

        with start_span("command.wrike.start", kind=Kind.CHAIN) as span:
            span.set_attribute("user.id", slack_user_id)
            span.set_attribute("user.name", real_name or "")
            if email and settings.trace_sensitive_data:
                span.set_attribute("user.email", email)
            span.set_attribute("user.slack_id", slack_user_id)
            span.set_attribute("user.db_id", str(user_id))
            span.set_attribute("user.team_id", slack_team_id)
            span.set_attribute("session.id", f"wrike:{slack_user_id}")
            span.set_attribute("command.name", "/wrike")
            span.set_attribute(SpanAttributes.INPUT_VALUE, "/wrike")
            span.set_attribute(SpanAttributes.INPUT_MIME_TYPE, "text/plain")

            # All conversation happens in the bot's DM with the user.
            dm = await client.conversations_open(users=slack_user_id)
            target_channel = dm["channel"]["id"]
            invoked_in_dm = body.get("channel_id") == target_channel

            try:
                tasks = await _list_tasks_for_user(user_id)  # type: ignore[arg-type]
            except wrike_int.WrikeNotConnectedError:
                await client.chat_postMessage(
                    channel=target_channel,
                    text="Wrike isn't connected yet.",
                    blocks=connect_prompt_blocks(
                        "wrike",
                        slack_team_id=slack_team_id,
                        slack_user_id=slack_user_id,
                        reason=(
                            "Wrike isn't connected yet. Connect it, then run "
                            "`/wrike` again."
                        ),
                    ),
                )
                if not invoked_in_dm:
                    await client.chat_postEphemeral(
                        channel=body["channel_id"],
                        user=slack_user_id,
                        text="📬 I sent you a DM.",
                    )
                return

            slots = []
            try:
                slots = await _slots_for_horizon(
                    user_id=user_id,  # type: ignore[arg-type]
                    tz_name=tz_name,
                    workday_start=workday_start,
                    workday_end=workday_end,
                )
            except gcal.CalendarNotConnectedError:
                pass

        if not tasks:
            await client.chat_postMessage(
                channel=target_channel,
                text="No Wrike tasks in 'New' status assigned to you. 🎉",
            )
            if not invoked_in_dm:
                await client.chat_postEphemeral(
                    channel=body["channel_id"],
                    user=slack_user_id,
                    text="📬 I sent you a DM.",
                )
            return

        blocks: list[dict] = []
        blocks.extend(wb.task_list_blocks(tasks))
        blocks.extend(wb.slots_block(slots, tz_name))
        blocks.extend(wb.prompt_block())

        posted = await client.chat_postMessage(
            channel=target_channel, blocks=blocks, text="Pick a Wrike task to schedule"
        )
        thread_ts = posted["ts"]
        if not invoked_in_dm:
            await client.chat_postEphemeral(
                channel=body["channel_id"],
                user=slack_user_id,
                text="📬 I sent you a DM.",
            )

        await session_store.save_command_state(
            user_id=user_id,  # type: ignore[arg-type]
            channel_id=target_channel,
            thread_ts=thread_ts,
            command_name=SESSION_NAME,
            state={
                "tasks": tasks,
                "tz": tz_name,
                "workday_start": workday_start,
                "workday_end": workday_end,
                "slack_user_id": slack_user_id,
                "stage": "AWAITING_USER_INTENT",
            },
            ttl_minutes=30,
        )

    # NOTE: the in-thread message handler used to live here as an
    # `@app.event("message")`. It now runs from app.slack_app.handlers via a
    # call to `try_handle_wrike_thread_reply()` so we have ONE message handler
    # in the system. See app.slack_app.handlers.


_SCHEDULE_HINTS = (
    "schedule", "plot", "book", "calendar", "meeting", "block off",
    "tomorrow", "today", "yesterday", "next week", "this week",
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday",
    "am ", "pm ", " am", " pm",
    ":", "min", "hour",
)
_NON_SCHEDULE_HINTS = (
    "update", "set status", "set the status", "change status", "mark",
    "complete", "completed", "done", "in progress", "active",
    "post", "comment", "reply on", "say on", "note on",
)


def _looks_like_non_scheduling(text: str, state: dict) -> bool:
    """Heuristic: is this reply doing something other than scheduling?

    If we're already mid-state-machine (e.g. waiting on overlap override),
    the existing scheduler flow takes the reply. Otherwise, if the text
    contains action verbs ('update status', 'mark complete', 'post comment')
    and no obvious schedule signals (dates, times), route to the
    conversational agent.
    """
    if state.get("stage") in ("AWAITING_OVERRIDE",):
        return False
    lower = text.lower()
    has_action = any(kw in lower for kw in _NON_SCHEDULE_HINTS)
    has_schedule = any(kw in lower for kw in _SCHEDULE_HINTS)
    return has_action and not has_schedule


def build_task_list_context(tasks: list[dict]) -> str:
    """Build extra-system-context the conversational agent sees so it can map
    'task #N' or 'task 15' references to the right Wrike API task_id.

    Also re-states the Wrike URL handling rule here (rather than in the main
    system prompt) since the user is only likely to paste a Wrike URL in a
    /wrike thread context.
    """
    lines = [
        "[Context: the user is replying inside a /wrike task list. They may "
        "reference tasks by their list number (1-based) or by title. Resolve "
        "any 'task #N' / 'task 15' / 'the K+S Potash one' references to the "
        "task_id below, then pass that task_id to Wrike tools.]",
        "",
        "[Wrike URLs the user pastes use a numeric id in the URL (e.g. "
        "id=4449467731) which is NOT the API id. Pass the FULL URL as "
        "`task_ref` to any Wrike tool — the server resolves it to the "
        "alphanumeric API id (e.g. IEAA4BCD). Never use the numeric URL id "
        "directly.]",
        "",
        "Task list currently shown to user:",
    ]
    for i, t in enumerate(tasks, start=1):
        title = t.get("title") or "(untitled)"
        tid = t.get("id") or "(no-id)"
        lines.append(f"  {i}. {title}  —  task_id: {tid}")
    return "\n".join(lines)


async def try_handle_wrike_thread_reply(
    *, client, user_id: int, channel_id: str, thread_ts: str, user_text: str
) -> tuple[bool, str | None]:
    """Process a reply inside a /wrike thread.

    Returns:
      (handled, extra_context)
        handled=True  → this function processed the message, caller should stop
        handled=False → caller should run the conversational agent. If
                        extra_context is non-None, pass it as the agent's
                        per-turn system context (so the agent can resolve
                        'task #N' references).
    """
    state = await session_store.get_command_state(
        user_id=user_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        command_name=SESSION_NAME,
    )
    if state is None:
        return False, None

    # Non-scheduling intent (update status / post comment / etc) — fall back
    # to the conversational agent, but give it the task list as context.
    if _looks_like_non_scheduling(user_text, state):
        ctx = build_task_list_context(state.get("tasks") or [])
        return False, ctx

    await _handle_thread_turn(
        client=client,
        user_id=user_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        user_text=user_text,
        state=state,
    )
    return True, None


# ── Step 3: state-machine turn ─────────────────────────────────────────────


async def _handle_thread_turn(
    *,
    client,
    user_id: int,
    channel_id: str,
    thread_ts: str,
    user_text: str,
    state: dict,
) -> None:
    tz_name = state["tz"]
    tasks = state["tasks"]

    # NOTE: the old AWAITING_OVERRIDE text-based flow (user types "schedule
    # anyway" / "use suggested") has been replaced by an approval card with
    # buttons — see the overlap branch below.

    # Parse intent
    intent = await extract_schedule_intent(
        user_message=user_text,
        candidate_tasks=tasks,
        tz_name=tz_name,
        work_start=state.get("workday_start") or "09:00",
        work_end=state.get("workday_end") or "18:00",
    )

    # Merge with anything already stored in state (sticky slot filling)
    pending = state.get("pending_slot", {})
    task_index = intent.task_index or pending.get("task_index")
    date_iso = intent.date_iso or pending.get("date_iso")
    start_time = intent.start_time or pending.get("start_time")
    duration_minutes = intent.duration_minutes or pending.get("duration_minutes")

    state["pending_slot"] = {
        "task_index": task_index,
        "date_iso": date_iso,
        "start_time": start_time,
        "duration_minutes": duration_minutes,
    }

    missing: list[str] = []
    if task_index is None:
        missing.append(f"which task (1–{len(tasks)})")
    if date_iso is None:
        missing.append("the date")
    if start_time is None:
        missing.append("the start time")
    if duration_minutes is None:
        missing.append("the duration")

    if missing:
        await session_store.save_command_state(
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            command_name=SESSION_NAME,
            state={**state, "stage": "AWAITING_USER_INTENT"},
        )
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text="Got it — I still need: " + ", ".join(missing) + ".",
        )
        return

    # We have everything. Compute proposed slot in user_tz.
    z = user_tz(tz_name)
    try:
        d = datetime.fromisoformat(date_iso).date()
        h, m = (int(x) for x in start_time.split(":"))
        proposed_start = datetime.combine(d, datetime.min.time(), tzinfo=z).replace(hour=h, minute=m)
        proposed_end = proposed_start + timedelta(minutes=int(duration_minutes))
    except Exception:
        # Date/time parse failure on user input. The exception itself is usually
        # safe (e.g. "minute must be in 0..59"), but a generic message is safer
        # against future codepath changes that might surface internal details.
        logger.exception("wrike scheduling: date/time parse failed")
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=(
                "I couldn't parse that into a real time. "
                "Try a format like “tomorrow 9:00 for 1 hour” or “Fri 14:30 for 30 min”."
            ),
        )
        return

    # Sanity guards
    if proposed_start < datetime.now(tz=z) - timedelta(minutes=5):
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text="That start time is in the past — pick a future slot.",
        )
        return
    if int(duration_minutes) > 8 * 60:
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text="That's longer than 8 hours — pick a shorter duration.",
        )
        return

    # Resolve which task is being scheduled
    idx = task_index
    if idx is None or idx < 1 or idx > len(tasks):
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text="Lost track of which task — start over with `/wrike`.",
        )
        await session_store.clear_command_state(
            user_id=user_id, channel_id=channel_id, thread_ts=thread_ts
        )
        return
    task = tasks[idx - 1]
    task_id = task.get("id") or ""
    task_title = task.get("title") or "(untitled)"
    permalink = task.get("permalink") or ""
    task_desc = (task.get("description") or "")[:1000]

    # Helper that packages the "schedule_wrike_task" tool call for the
    # approval card's button payload.
    def _action_for(start_dt: datetime, end_dt: datetime, summary: str) -> dict:
        return {
            "tool": "schedule_wrike_task",
            "args": {
                "task_id": task_id,
                "title": task_title,
                "permalink": permalink,
                "description_extra": task_desc,
                "start_iso": start_dt.isoformat(),
                "end_iso": end_dt.isoformat(),
                "tz_name": tz_name,
            },
            "summary": summary,
        }

    # Overlap check (skip if force_overlap requested by extractor). When we
    # need conflict handling, fetch the 5-day horizon once and reuse it for the
    # suggested alternate slot instead of doing a second Calendar pull.
    horizon_busy: list[TimeSlot] = []
    if intent.force_overlap:
        overlapping = []
    else:
        horizon_events = await gcal.list_events(
            user_id,
            time_min=proposed_start - timedelta(hours=4),
            time_max=proposed_start + timedelta(days=5),
        )
        overlapping = gcal.find_overlaps_in_events(
            horizon_events, proposed_start, proposed_end
        )
        horizon_busy = gcal.busy_slots_from_events(horizon_events)

    def fmt_day_time(s: datetime, e: datetime) -> str:
        return f"{s.strftime('%a %b %-d, %-I:%M%p')}–{e.strftime('%-I:%M%p')}"

    safe_task_title = escape_slack_text(task_title)
    proposed_summary = (
        f"Schedule *{safe_task_title}* at *{fmt_day_time(proposed_start, proposed_end)}* "
        f"(also marks the Wrike task as *Accepted & Scheduled*)."
    )

    if not overlapping:
        # Clean slot — single approval card
        blocks = build_approval_blocks_with_alternates(
            primary=_action_for(proposed_start, proposed_end, proposed_summary),
        )
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text="Approval needed",
            blocks=blocks,
        )
        await session_store.clear_command_state(
            user_id=user_id, channel_id=channel_id, thread_ts=thread_ts
        )
        return

    # Overlap → offer both options on one card
    suggested = first_free_slot_for_duration(
        busy=horizon_busy,
        duration_minutes=int(duration_minutes),
        horizon_days=5,
        workday_start=state["workday_start"],
        workday_end=state["workday_end"],
        tz_name=tz_name,
        not_before=proposed_start,
    )
    overlap_lines = "\n".join(
        f"  • {escape_slack_text(ov.get('title') or '(untitled)')}"
        for ov in overlapping[:3]
    )
    intro = (
        f"🔔 *Approval needed* — ⚠️ *Overlap detected.*\n"
        f"{fmt_day_time(proposed_start, proposed_end)} overlaps with:\n"
        f"{overlap_lines}\n\n"
        f"Pick one option below."
    )
    primary_action = _action_for(
        proposed_start,
        proposed_end,
        f"*Schedule anyway* at *{fmt_day_time(proposed_start, proposed_end)}* "
        f"(overlap will remain). Marks Wrike task as *Accepted & Scheduled*.",
    )
    alt_action = None
    if suggested:
        alt_action = _action_for(
            suggested.start,
            suggested.end,
            f"*Use suggested slot* — *{fmt_day_time(suggested.start, suggested.end)}* "
            f"(no overlap). Marks Wrike task as *Accepted & Scheduled*.",
        )

    blocks = build_approval_blocks_with_alternates(
        intro_text=intro,
        primary=primary_action,
        alternate=alt_action,
        primary_button_text="✅ Schedule anyway",
        alternate_button_text="🔁 Use suggested slot",
    )
    await client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text="Approval needed (overlap)",
        blocks=blocks,
    )
    await session_store.clear_command_state(
        user_id=user_id, channel_id=channel_id, thread_ts=thread_ts
    )


async def _busy_for_horizon(user_id: int, anchor: datetime, days: int) -> list[TimeSlot]:
    events = await gcal.list_events(
        user_id,
        time_min=anchor - timedelta(hours=4),
        time_max=anchor + timedelta(days=days),
    )
    return [s for s in (gcal.event_to_busy_slot(ev) for ev in events) if s]


async def _find_overlaps(
    user_id: int, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    events = await gcal.list_events(
        user_id, time_min=start - timedelta(hours=4), time_max=end + timedelta(hours=4)
    )
    overlaps: list[dict] = []
    for ev in events:
        if gcal.event_response_status(ev, None) == "declined":
            continue
        slot = gcal.event_to_busy_slot(ev)
        if slot is None:
            continue
        if slot.start < end and start < slot.end:
            overlaps.append(
                {
                    "title": ev.get("summary"),
                    "start": ev["start"]["dateTime"],
                    "end": ev["end"]["dateTime"],
                }
            )
    return overlaps


# NOTE: `_create_event_and_finish` was removed when /wrike switched to the
# approval-card flow. The actual create+status-update now happens in the
# `schedule_wrike_task` tool, invoked by the Approve button handler.
