"""/wrike — list New tasks, then drive a multi-turn flow to plot one onto the calendar."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from app.db.engine import session_scope
from app.db.users import get_or_create_user
from app.formatters import wrike_blocks as wb
from app.integrations import google_calendar as gcal
from app.integrations import wrike as wrike_int
from app.llm.extract_schedule import extract_schedule_intent
from app.observability import get_tracer
from app.sessions import store as session_store
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

        tracer = get_tracer()
        with tracer.start_as_current_span("command.wrike.start") as span:
            span.set_attribute("user.id", slack_user_id)
            span.set_attribute("user.name", real_name or "")
            if email:
                span.set_attribute("user.email", email)
            span.set_attribute("user.slack_id", slack_user_id)
            span.set_attribute("user.db_id", str(user_id))
            span.set_attribute("user.team_id", slack_team_id)
            span.set_attribute("session.id", f"wrike:{slack_user_id}")
            span.set_attribute("command.name", "/wrike")
            span.set_attribute("input.value", "/wrike")
            span.set_attribute("input.mime_type", "text/plain")

            # All conversation happens in the bot's DM with the user.
            dm = await client.conversations_open(users=slack_user_id)
            target_channel = dm["channel"]["id"]
            invoked_in_dm = body.get("channel_id") == target_channel

            try:
                tasks = await _list_tasks_for_user(user_id)  # type: ignore[arg-type]
            except wrike_int.WrikeNotConnectedError:
                await client.chat_postMessage(
                    channel=target_channel,
                    text="Wrike isn't connected yet — run `/connect` to set it up.",
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
                text="📬 Sent the task list to our DM.",
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


async def try_handle_wrike_thread_reply(
    *, client, user_id: int, channel_id: str, thread_ts: str, user_text: str
) -> bool:
    """If this thread has an active /wrike state, drive the next turn. Returns True if handled."""
    state = await session_store.get_command_state(
        user_id=user_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        command_name=SESSION_NAME,
    )
    if state is None:
        return False

    await _handle_thread_turn(
        client=client,
        user_id=user_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        user_text=user_text,
        state=state,
    )
    return True


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

    # Detect simple "use suggested" / "schedule anyway" / "cancel" replies first
    lower = user_text.strip().lower()
    if state.get("stage") == "AWAITING_OVERRIDE":
        if lower in ("cancel", "stop", "abort"):
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts, text="Cancelled — nothing scheduled."
            )
            await session_store.clear_command_state(
                user_id=user_id, channel_id=channel_id, thread_ts=thread_ts
            )
            return
        if "use suggested" in lower or "use the suggested" in lower or "use alt" in lower:
            sug = state.get("suggested_slot")
            if sug:
                await _create_event_and_finish(
                    client=client,
                    user_id=user_id,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    state=state,
                    start_iso=sug["start"],
                    end_iso=sug["end"],
                )
                return
        if "anyway" in lower or "force" in lower or "yes" in lower:
            await _create_event_and_finish(
                client=client,
                user_id=user_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                state=state,
                start_iso=state["proposed_start"],
                end_iso=state["proposed_end"],
            )
            return
        # Otherwise fall through and re-extract — the user gave a new time.

    # Parse intent
    intent = await extract_schedule_intent(
        user_message=user_text, candidate_tasks=tasks, tz_name=tz_name
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
    except Exception as exc:
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=f"I couldn't parse that into a real time: `{exc}`. Try again.",
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
            text="That's longer than 8 hours — confirm by saying *force* if you really mean it.",
        )

    # If user previously said force_overlap, skip the check
    if intent.force_overlap:
        await _create_event_and_finish(
            client=client,
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            state=state,
            start_iso=proposed_start.isoformat(),
            end_iso=proposed_end.isoformat(),
        )
        return

    # Overlap check
    overlapping = await _find_overlaps(user_id, proposed_start, proposed_end)
    if overlapping:
        suggested = first_free_slot_for_duration(
            busy=await _busy_for_horizon(user_id, proposed_start, days=5),
            duration_minutes=int(duration_minutes),
            horizon_days=5,
            workday_start=state["workday_start"],
            workday_end=state["workday_end"],
            tz_name=tz_name,
            not_before=proposed_start,
        )
        new_state = {
            **state,
            "stage": "AWAITING_OVERRIDE",
            "proposed_start": proposed_start.isoformat(),
            "proposed_end": proposed_end.isoformat(),
            "suggested_slot": (
                {"start": suggested.start.isoformat(), "end": suggested.end.isoformat()}
                if suggested
                else None
            ),
        }
        await session_store.save_command_state(
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            command_name=SESSION_NAME,
            state=new_state,
        )
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            blocks=wb.overlap_warning(proposed_start, proposed_end, overlapping, suggested, tz_name),
            text="Schedule conflict",
        )
        return

    # No conflict → create
    await _create_event_and_finish(
        client=client,
        user_id=user_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        state=state,
        start_iso=proposed_start.isoformat(),
        end_iso=proposed_end.isoformat(),
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


async def _create_event_and_finish(
    *,
    client,
    user_id: int,
    channel_id: str,
    thread_ts: str,
    state: dict,
    start_iso: str,
    end_iso: str,
) -> None:
    tasks = state["tasks"]
    pending = state.get("pending_slot", {})
    idx = pending.get("task_index")
    if idx is None or idx < 1 or idx > len(tasks):
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts, text="Lost track of which task — start over with `/wrike`."
        )
        await session_store.clear_command_state(
            user_id=user_id, channel_id=channel_id, thread_ts=thread_ts
        )
        return
    task = tasks[idx - 1]
    tz_name = state["tz"]

    start_dt = datetime.fromisoformat(start_iso)
    end_dt = datetime.fromisoformat(end_iso)

    description = (
        f"Wrike task: {task.get('title')}\n"
        f"Status: New\n"
        f"Permalink: {task.get('permalink')}\n\n"
        f"{(task.get('description') or '')[:1000]}\n\n"
        f"— Scheduled via Slack /wrike at {datetime.utcnow().isoformat()}Z"
    )

    tracer = get_tracer()
    with tracer.start_as_current_span("gcal.create_event") as span:
        span.set_attribute("wrike.task_id", task.get("id") or "")
        span.set_attribute("duration.minutes", int((end_dt - start_dt).total_seconds() / 60))
        span.set_attribute("override.used", state.get("stage") == "AWAITING_OVERRIDE")

        try:
            event = await gcal.create_event(
                user_id,
                summary=task.get("title") or "(untitled Wrike task)",
                description=description,
                start=start_dt,
                end=end_dt,
                tz_name=tz_name,
                color_id="5",
                extended_properties={
                    "wrikeTaskId": task.get("id") or "",
                    "createdBy": "slack-assistant",
                },
                source_title="Wrike task",
                source_url=task.get("permalink") or "",
            )
        except Exception as exc:
            logger.exception("Calendar event create failed")
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts, text=f"⚠️ Failed to create event: `{exc}`"
            )
            return

    await client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        blocks=wb.created_block(
            title=task.get("title") or "Wrike task",
            start=start_dt,
            end=end_dt,
            tz_name=tz_name,
            link=task.get("permalink") or event.get("htmlLink") or "",
        ),
        text="Scheduled",
    )
    await session_store.clear_command_state(
        user_id=user_id, channel_id=channel_id, thread_ts=thread_ts
    )
