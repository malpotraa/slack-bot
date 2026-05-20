"""Single Bolt message handler.

The bot only listens inside its own DM ("Messages" tab). For each user message:
  1. If this is a thread reply on an active `/wrike` state, drive that flow.
  2. Otherwise, run the conversational agent and post the reply IN-THREAD so
     follow-ups in the same thread share context.

Streaming: we post a "🌀 Thinking…" placeholder, then chat_update it with
partial text as Anthropic streams tokens (debounced to ~700ms per update to
avoid Slack rate limits). `finalize()` posts the clean final text without the
streaming cursor.
"""

from __future__ import annotations

import asyncio
import re
import time

from loguru import logger
from slack_sdk.web.async_client import AsyncWebClient

from app.agent.runner import run_agent_turn
from app.config import settings
from app.db.engine import session_scope
from app.db.users import get_or_create_user
from app.sessions import store as session_store
from app.slack_app.approval import (
    build_approval_blocks,
    build_approval_blocks_with_alternates,
)
from app.slack_app.commands.wrike_cmd import try_handle_wrike_thread_reply
from app.utils.slack_mrkdwn import to_slack_mrkdwn


class SlackStreamUpdater:
    """Debounced chat.update writer for token-streamed agent replies."""

    def __init__(
        self,
        client: AsyncWebClient,
        channel: str,
        ts: str,
        *,
        min_interval: float = 0.7,
        cursor: str = " ▌",
    ) -> None:
        self.client = client
        self.channel = channel
        self.ts = ts
        self.min_interval = min_interval
        self.cursor = cursor
        self._latest_text = ""
        self._last_flush = 0.0
        self._lock = asyncio.Lock()

    async def push(self, accumulated_text: str) -> None:
        """Called per token delta. Flushes at most every min_interval."""
        self._latest_text = accumulated_text
        if time.monotonic() - self._last_flush < self.min_interval:
            return
        await self._flush(with_cursor=True)

    async def _flush(self, *, with_cursor: bool) -> None:
        async with self._lock:
            text = self._latest_text
            if not text:
                return
            display = to_slack_mrkdwn(text) + (self.cursor if with_cursor else "")
            try:
                await self.client.chat_update(
                    channel=self.channel, ts=self.ts, text=display
                )
                self._last_flush = time.monotonic()
            except Exception as exc:
                logger.warning(f"streaming chat_update failed: {exc}")

    async def finalize(self, final_text: str) -> None:
        """Replace the streaming view with the clean final text (no cursor)."""
        self._latest_text = final_text
        async with self._lock:
            try:
                await self.client.chat_update(
                    channel=self.channel,
                    ts=self.ts,
                    text=to_slack_mrkdwn(final_text),
                )
            except Exception as exc:
                logger.warning(f"final chat_update failed: {exc}")


_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _strip_mentions(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


def _create_background_task(coro) -> None:
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)

    def _done(done: asyncio.Task) -> None:
        _BACKGROUND_TASKS.discard(done)
        try:
            done.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(f"Slack background task failed: {exc}")

    task.add_done_callback(_done)


def _approval_button_labels(action: dict) -> tuple[str, str]:
    """Pick (primary, alternate) button labels per tool + conflict state.

    The alternate label always interpolates the suggested slot's wall-clock
    time when one is available, so users see "🔁 Use 11:30am" instead of a
    generic "🔁 Use suggested slot".
    """
    tool = action["tool"]
    alternate = action.get("alternate") or {}
    short = alternate.get("short_time")
    alt_label = f"🔁 Use {short}" if short else "🔁 Use suggested slot"

    if alternate:
        # Conflict path — the primary commits the user's original times anyway.
        if tool == "create_calendar_event":
            return "✅ Create anyway", alt_label
        if tool == "update_calendar_event":
            return "✅ Move anyway", alt_label
        return "✅ Confirm", alt_label

    # No conflict — short, neutral verbs.
    if tool == "create_calendar_event":
        return "✅ Create", alt_label
    if tool == "update_calendar_event":
        args = action.get("args") or {}
        time_changed = bool(args.get("start_iso") or args.get("end_iso"))
        title_changed = bool(args.get("title"))
        description_changed = args.get("description") is not None
        if time_changed and not title_changed and not description_changed:
            return "✅ Move", alt_label
        if title_changed and not time_changed and not description_changed:
            return "✅ Rename", alt_label
        return "✅ Update", alt_label
    if tool == "schedule_wrike_task":
        return "✅ Schedule", alt_label
    if tool == "post_wrike_task_comment":
        return "✅ Post", alt_label
    if tool in {"update_wrike_task_status", "update_working_hours"}:
        return "✅ Update", alt_label
    if tool == "update_user_notes":
        return "✅ Save notes", alt_label
    return "✅ Confirm", alt_label


async def _handle_dm_message(*, body: dict, client: AsyncWebClient, event: dict) -> None:
    if event.get("subtype") in ("bot_message", "message_changed", "message_deleted"):
        return
    if event.get("bot_id"):
        return

    slack_team_id = body.get("team_id") or ""
    slack_user_id = event.get("user") or ""
    if not slack_user_id:
        return

    text = _strip_mentions(event.get("text") or "")
    if not text:
        return

    channel_id = event.get("channel") or ""
    msg_ts = event.get("ts") or ""
    thread_ts = event.get("thread_ts") or msg_ts

    logger.info(
        f"DM message: user={slack_user_id} channel={channel_id} "
        f"thread_ts={thread_ts} is_reply={bool(event.get('thread_ts'))} "
        f"text={text[:60]!r}"
    )

    # Hydrate user profile (timezone, name, email) before the single user upsert.
    try:
        user_info_resp = await client.users_info(user=slack_user_id)
        u = user_info_resp.get("user", {}) or {}
        profile = u.get("profile", {}) or {}
        tz = u.get("tz") or "UTC"
        email = profile.get("email")
        real_name = u.get("real_name") or profile.get("real_name") or u.get("name")
    except Exception as exc:
        logger.warning(f"users_info failed: {exc}")
        tz = "UTC"
        email = None
        real_name = None

    # Resolve / upsert the User row once.
    async with session_scope() as session:
        user = await get_or_create_user(
            session,
            slack_team_id=slack_team_id,
            slack_user_id=slack_user_id,
            email=email,
            real_name=real_name,
            tz=tz,
        )
        user_id = user.id
        if user_id is None:
            logger.error("user_id None after get_or_create_user")
            return
        workday_start = user.workday_start
        workday_end = user.workday_end
        user_notes = user.notes

    # 1) If this is a reply inside an active /wrike state machine thread, try
    # to route there. The helper may return extra_context (the task list) when
    # the reply isn't a scheduling intent — we pass that to the agent below.
    extra_context: str | None = None
    if event.get("thread_ts"):
        handled, extra_context = await try_handle_wrike_thread_reply(
            client=client,
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_text=text,
        )
        if handled:
            logger.info(f"  → dispatched to /wrike state machine (user={user_id})")
            return
        if extra_context:
            logger.info("  → non-scheduling /wrike reply; forwarding to agent with task-list context")

    # 2) Conversational agent. Reply lands as a thread on the user's message
    #    so subsequent in-thread replies share session memory.

    # Add 👀 reaction (fire-and-forget; we don't await the result for the hot path)
    _create_background_task(_react_eyes(client, channel_id, msg_ts))

    # Post the streaming placeholder in-thread.
    try:
        loading = await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text="🌀 Thinking…",
        )
        loading_ts = loading["ts"]
    except Exception:
        logger.exception("Failed to post loading placeholder")
        loading_ts = None

    # Load thread context.
    history = await session_store.load_agent_history(
        user_id=user_id, channel_id=channel_id, thread_ts=thread_ts
    )
    logger.info(f"  → agent run with {len(history)} prior messages in history")

    # Wire the streaming callback when we have a placeholder to update into.
    updater: SlackStreamUpdater | None = None
    on_chunk = None
    if loading_ts:
        updater = SlackStreamUpdater(client, channel_id, loading_ts)
        on_chunk = updater.push

    pending_actions: list[dict] = []
    try:
        reply, pending_actions = await run_agent_turn(
            user_id=user_id,
            user_name=real_name or "there",
            user_email=email,
            user_tz=tz,
            workday_start=workday_start,
            workday_end=workday_end,
            user_notes=user_notes,
            history=history,
            user_message=text,
            extra_system_context=extra_context,
            slack_user_id=slack_user_id,
            slack_team_id=slack_team_id,
            slack_bot_token=settings.slack_bot_token,
            channel_id=channel_id,
            thread_ts=thread_ts,
            on_stream_chunk=on_chunk,
        )
    except Exception:
        # Full traceback is captured by Phoenix + Cloud Logging via the OTel
        # auto-instrumentation and loguru. The user-facing message stays
        # generic so we don't leak DB schemas, internal paths, or stack frames.
        logger.exception("agent turn failed")
        reply = "⚠️ Something went wrong on my end. Try again in a moment — if it keeps failing, check the Phoenix dashboard for the trace."

    # Replace the placeholder with the clean final reply.
    if updater is not None:
        await updater.finalize(reply)
    elif loading_ts:
        try:
            await client.chat_update(
                channel=channel_id, ts=loading_ts, text=to_slack_mrkdwn(reply)
            )
        except Exception:
            await client.chat_postMessage(
                channel=channel_id, thread_ts=thread_ts, text=to_slack_mrkdwn(reply)
            )
    else:
        await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts, text=to_slack_mrkdwn(reply)
        )

    # If the agent proposed any writes, post an approval card per action.
    # The card is flat: the tool's `summary_for_user` already contains the
    # title line, current → new, and any conflict/alternate notes. We only
    # pick button labels here based on tool + whether an alternate exists.
    for action in pending_actions:
        primary_text, alt_text = _approval_button_labels(action)
        alternate = action.get("alternate")
        if alternate:
            blocks = build_approval_blocks_with_alternates(
                primary={
                    "tool": action["tool"],
                    "args": action.get("args") or {},
                    "summary": action.get("summary") or "",
                },
                alternate=alternate,
                primary_button_text=primary_text,
                alternate_button_text=alt_text,
            )
        else:
            blocks = build_approval_blocks(
                [action],
                primary_button_text=primary_text,
            )
        try:
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="Approval needed",
                blocks=blocks,
            )
        except Exception as exc:
            logger.warning(f"failed to post approval card: {exc}")

    # Persist the turn so follow-ups in this thread have context.
    await session_store.append_agent_turn(
        user_id=user_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        user_msg=text,
        assistant_msg=reply,
    )


async def _react_eyes(client: AsyncWebClient, channel: str, ts: str) -> None:
    try:
        await client.reactions_add(channel=channel, timestamp=ts, name="eyes")
    except Exception:
        pass


def register_message_handlers(app) -> None:
    @app.event("message")
    async def on_message(body, event, client):
        # Only respond inside the bot's own DM ("Messages" tab).
        if event.get("channel_type") != "im":
            return
        await _handle_dm_message(body=body, client=client, event=event)
