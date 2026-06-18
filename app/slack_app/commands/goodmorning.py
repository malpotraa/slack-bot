"""/goodmorning — parallel briefing across Slack, Wrike, and Calendar."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from loguru import logger

from app.config import settings
from app.db.engine import session_scope
from app.db.users import get_or_create_user
from app.formatters.briefing import briefing_blocks
from app.integrations import google_calendar as gcal
from app.integrations import slack_search
from app.integrations import wrike as wrike_int
from openinference.semconv.trace import OpenInferenceSpanKindValues as Kind
from openinference.semconv.trace import SpanAttributes

from app.observability import start_span
from app.slack_app import connect_prompt
from app.slack_app.connection_status import status_for
from app.utils.timezone import end_of_day, now_in, start_of_day
from app.utils.working_hours import (
    free_busy_summary,
    working_window,
)


async def _fetch_mentions(
    *, user_id: int, slack_user_id: str
) -> list[slack_search.UnrepliedMention]:
    try:
        return await slack_search.find_unreplied_mentions(
            user_id=user_id,
            slack_user_id=slack_user_id,
            days=7,
            bot_token=settings.slack_bot_token,
        )
    except slack_search.SlackUserNotConnectedError:
        return []
    except Exception as exc:
        logger.warning(f"slack mentions fetch failed: {exc}")
        return []


async def _fetch_wrike(*, user_id: int, tz_name: str) -> tuple[list[dict], list[dict]]:
    """Return (now_status_tasks, due_soon_tasks)."""
    try:
        client = await wrike_int.WrikeClient.for_user(user_id)
        me = await client.me()
        me_id = me.get("id")
        if not me_id:
            return [], []

        # Resolve EVERY "New" status across every workflow (Wrike workspaces can
        # have many workflows, each with its own "New" with a distinct ID).
        new_status_ids = await wrike_int.resolve_status_ids(user_id, "New")

        # Note: Wrike's `fields` param only accepts a fixed list of optional fields.
        # `customStatuses`, `dueDate`, and `permalink` are NOT valid here — they either
        # come back by default (customStatusId, dates) or aren't requestable on list calls.
        fields = ["responsibleIds"]
        async def new_tasks_fn():
            if not new_status_ids:
                return []
            return await client.tasks(
                responsibles=[me_id], custom_statuses=new_status_ids, fields=fields
            )

        async def due_soon_fn():
            now_dt = now_in(tz_name)
            return await client.tasks(
                responsibles=[me_id],
                due_date_start=now_dt,
                due_date_end=now_dt + timedelta(days=2),
                status="Active",
                fields=fields,
            )

        new_tasks, due_soon = await asyncio.gather(new_tasks_fn(), due_soon_fn())
        seen = {t["id"] for t in new_tasks}
        due_soon = [t for t in due_soon if t["id"] not in seen]
        return [_summarize_task(t) for t in new_tasks], [_summarize_task(t) for t in due_soon]
    except wrike_int.WrikeNotConnectedError:
        return [], []
    except Exception as exc:
        logger.warning(f"wrike fetch failed: {exc}")
        return [], []


def _summarize_task(t: dict) -> dict:
    due = (t.get("dates") or {}).get("due")
    return {
        "id": t.get("id"),
        "title": t.get("title"),
        "due_date": due,
        "permalink": wrike_int.task_permalink(t),
    }


async def _fetch_calendar(*, user_id: int, tz_name: str, workday_start: str, workday_end: str):
    try:
        today = now_in(tz_name)
        tmin = start_of_day(today, tz_name)
        tmax = end_of_day(today, tz_name)
        events = await gcal.list_events(user_id, time_min=tmin, time_max=tmax)
        # Build summary list for the formatter
        summaries: list[dict[str, Any]] = []
        busy_slots = []
        for ev in events:
            slot = gcal.event_to_busy_slot(ev)
            if slot:
                busy_slots.append(slot)
            summaries.append(
                {
                    "title": ev.get("summary") or "(untitled)",
                    "start": ev.get("start", {}).get("dateTime")
                    or ev.get("start", {}).get("date"),
                    "end": ev.get("end", {}).get("dateTime") or ev.get("end", {}).get("date"),
                    "is_all_day": gcal.event_is_all_day(ev),
                    "category": gcal.classify_event(ev),
                }
            )
        ww = working_window(today, workday_start, workday_end, tz_name)
        busy_total, free_total, free_slots = free_busy_summary(ww, busy_slots)
        return summaries, busy_total, free_total, free_slots
    except gcal.CalendarNotConnectedError:
        return [], timedelta(0), timedelta(0), []
    except Exception as exc:
        logger.warning(f"calendar fetch failed: {exc}")
        return [], timedelta(0), timedelta(0), []


def register(app):
    @app.command("/goodmorning")
    async def handle(ack, body, client):
        await ack()
        slack_team_id = body["team_id"]
        slack_user_id = body["user_id"]

        user_info = await client.users_info(user=slack_user_id)
        u = user_info.get("user", {})
        tz_name = u.get("tz") or "UTC"
        real_name = (
            u.get("real_name")
            or (u.get("profile") or {}).get("real_name")
            or u.get("name")
            or "there"
        )
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
            ws = user.workday_start
            we = user.workday_end

        # Resolve the bot's DM with the user up front so we can show progress there.
        dm = await client.conversations_open(users=slack_user_id)
        target_channel = dm["channel"]["id"]
        invoked_in_dm = body.get("channel_id") == target_channel

        # Tell the user we got it (channel echo if invoked outside DM)
        if not invoked_in_dm:
            await client.chat_postEphemeral(
                channel=body["channel_id"],
                user=slack_user_id,
                text="📬 I sent you a DM.",
            )

        # Post a temporary loading message in the DM. We'll replace it via chat_update
        # once the briefing is ready.
        loading = await client.chat_postMessage(
            channel=target_channel,
            text="🌀 Pulling your morning briefing — Slack mentions, Wrike tasks, calendar…",
        )
        loading_ts = loading["ts"]

        try:
            status = await status_for(slack_team_id, slack_user_id)
            missing_providers: list[str] = []
            if not status.google:
                missing_providers.append("google")
            if not status.wrike:
                missing_providers.append("wrike")
            if not status.slack_user_token:
                missing_providers.append("slack_user")

            with start_span("command.goodmorning", kind=Kind.CHAIN) as span:
                # Rich attributes for Braintrust
                span.set_attribute("user.id", slack_user_id)
                span.set_attribute("user.name", real_name or "")
                if email and settings.trace_sensitive_data:
                    span.set_attribute("user.email", email)
                span.set_attribute("user.slack_id", slack_user_id)
                span.set_attribute("user.db_id", str(user_id))
                span.set_attribute("user.team_id", slack_team_id)
                span.set_attribute("session.id", f"goodmorning:{slack_user_id}")
                span.set_attribute("command.name", "/goodmorning")
                span.set_attribute(SpanAttributes.INPUT_VALUE, "/goodmorning")
                span.set_attribute(SpanAttributes.INPUT_MIME_TYPE, "text/plain")

                mentions, wrike_pair, cal_data = await asyncio.gather(
                    _fetch_mentions(user_id=user_id, slack_user_id=slack_user_id),  # type: ignore[arg-type]
                    _fetch_wrike(user_id=user_id, tz_name=tz_name),  # type: ignore[arg-type]
                    _fetch_calendar(  # type: ignore[arg-type]
                        user_id=user_id,
                        tz_name=tz_name,
                        workday_start=ws,
                        workday_end=we,
                    ),
                )
                wrike_now, wrike_due_soon = wrike_pair
                cal_events, busy_total, free_total, free_slots = cal_data
                span.set_attribute("result.mentions_count", len(mentions))
                span.set_attribute("result.wrike_new_count", len(wrike_now))
                span.set_attribute("result.wrike_due_soon_count", len(wrike_due_soon))
                span.set_attribute("result.calendar_events_count", len(cal_events))
                span.set_attribute("result.busy_minutes", int(busy_total.total_seconds() // 60))
                span.set_attribute("result.free_minutes", int(free_total.total_seconds() // 60))

            blocks = briefing_blocks(
                user_name=real_name,
                today=now_in(tz_name),
                tz_name=tz_name,
                mentions=mentions,
                wrike_now=wrike_now,
                wrike_due_soon=wrike_due_soon,
                calendar_events=cal_events,
                busy_total=busy_total,
                free_total=free_total,
                free_slots=free_slots,
            )

            if missing_providers:
                labels = ", ".join(
                    f"*{connect_prompt.provider_label(p)}*" for p in missing_providers
                )
                blocks.append({"type": "divider"})
                blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"⚠️ Not yet connected: {labels}. "
                                "Connect to unlock the full briefing:"
                            ),
                        },
                    }
                )
                blocks.append(
                    {
                        "type": "actions",
                        "elements": [
                            connect_prompt.connect_button(
                                p,
                                slack_team_id=slack_team_id,
                                slack_user_id=slack_user_id,
                            )
                            for p in missing_providers
                        ],
                    }
                )

            # Replace the loading message with the real briefing.
            await client.chat_update(
                channel=target_channel,
                ts=loading_ts,
                text="Good morning!",
                blocks=blocks,
            )
        except Exception:
            logger.exception("goodmorning failed")
            await client.chat_update(
                channel=target_channel,
                ts=loading_ts,
                text=(
                    "⚠️ Couldn't build the briefing this time. "
                    "Trace is in Braintrust; try again in a moment."
                ),
            )
