"""Single Bolt message handler + the Google Ads drill-down button.

The bot only listens inside its own DM ("Messages" tab). For each user message:
  1. If this is a thread reply on an active `/wrike` state, drive that flow.
  2. Otherwise, run the conversational agent and post the reply IN-THREAD so
     follow-ups in the same thread share context.

Streaming: we post a "🌀 Thinking…" placeholder, then chat_update it with
partial text as Anthropic streams tokens (debounced to ~700ms per update to
avoid Slack rate limits). `finalize()` posts the clean final text.

A campaign "🔍" button on a Google Ads overview card runs the same agent path
with a synthetic "drill into campaign X" message — no parallel code path.
"""

from __future__ import annotations

import asyncio
import json
import re
import time

from loguru import logger
from slack_sdk.web.async_client import AsyncWebClient

from app.agent.runner import run_agent_turn
from app.config import settings
from app.db.engine import session_scope
from app.db.users import get_or_create_user
from app.formatters.ads_blocks import ADS_DRILL_ACTION
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

    async def status(self, text: str) -> None:
        """Show a transient status line in the placeholder (e.g. while a slow
        tool runs). Overwritten by the next streamed token."""
        self._latest_text = ""
        async with self._lock:
            try:
                await self.client.chat_update(
                    channel=self.channel, ts=self.ts, text=text
                )
            except Exception as exc:
                logger.warning(f"status chat_update failed: {exc}")

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

# Command-state name marking a thread as a Google Ads thread. Written after a
# Google Ads pull (here, and by /kpi's approval handler); read on every thread
# reply so follow-ups reuse the account without re-specifying it.
ADS_QA_SESSION = "ads_qa"

# Placeholder text shown while a slow tool runs, so the wait isn't a blank spin.
_TOOL_STATUS = {
    "get_google_ads_data": "🌀 Pulling Google Ads data — campaigns, trends, charts…",
}


def _strip_mentions(text: str) -> str:
    return _MENTION_RE.sub("", text or "").strip()


def _ads_thread_context(state: dict) -> str:
    """System context injected on replies inside a Google Ads thread so the
    agent reuses the established account instead of re-asking which one."""
    name = state.get("account_name") or "the account"
    cid = state.get("customer_id") or ""
    campaign = state.get("last_campaign_name") or ""
    campaign_note = (
        f" The most recent campaign discussed in this thread was "
        f"*{campaign}*; for follow-ups like \"graph it\" or \"trend\", pass "
        f"campaign_name={campaign!r} unless the user names a different campaign."
        if campaign
        else ""
    )
    return (
        f"[Google Ads thread context: this thread is already about the Google "
        f"Ads account *{name}* (customer_id {cid}). For any Google Ads "
        f"question in this thread, call get_google_ads_data with "
        f"customer_id={cid} — do NOT ask which account again. Only switch "
        f"accounts if the user explicitly names a different one."
        f"{campaign_note}]"
    )


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
    """Pick (primary, alternate) button labels per tool + conflict state."""
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


# ── User hydration helpers ─────────────────────────────────────────────────


async def _hydrate_user_profile(
    client: AsyncWebClient, slack_user_id: str
) -> tuple[str, str | None, str | None]:
    """Return (tz, email, real_name) from the Slack profile, best-effort."""
    try:
        resp = await client.users_info(user=slack_user_id)
        u = resp.get("user", {}) or {}
        profile = u.get("profile", {}) or {}
        return (
            u.get("tz") or "UTC",
            profile.get("email"),
            u.get("real_name") or profile.get("real_name") or u.get("name"),
        )
    except Exception as exc:
        logger.warning(f"users_info failed: {exc}")
        return ("UTC", None, None)


async def _resolve_user(
    *,
    slack_team_id: str,
    slack_user_id: str,
    tz: str,
    email: str | None,
    real_name: str | None,
) -> tuple[int | None, str, str, str | None]:
    """Upsert the User row; return (user_id, workday_start, workday_end, notes)."""
    async with session_scope() as session:
        user = await get_or_create_user(
            session,
            slack_team_id=slack_team_id,
            slack_user_id=slack_user_id,
            email=email,
            real_name=real_name,
            tz=tz,
        )
        if user.id is None:
            return (None, "09:00", "18:00", None)
        return (user.id, user.workday_start, user.workday_end, user.notes)


# ── Posting tool output ────────────────────────────────────────────────────


async def _post_tool_message(
    client: AsyncWebClient, channel_id: str, thread_ts: str, tool_msg: dict
) -> None:
    """Post a tool-emitted message: its Block Kit card, then any chart images."""
    blocks = tool_msg.get("blocks")
    if blocks:
        try:
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text=tool_msg.get("text") or "Result",
                blocks=blocks,
            )
        except Exception as exc:
            logger.warning(f"failed to post tool message: {exc}")

    images = tool_msg.get("images") or []
    file_uploads = [
        {
            "content": img["png"],
            "filename": img.get("filename") or "chart.png",
            "title": img.get("title") or "Chart",
        }
        for img in images
        if img.get("png")
    ]
    if file_uploads:
        try:
            await client.files_upload_v2(
                channel=channel_id,
                thread_ts=thread_ts,
                file_uploads=file_uploads,
            )
        except Exception as exc:
            logger.warning(f"failed to upload chart images: {exc}")


async def _save_ads_thread_state(
    *, user_id: int, channel_id: str, thread_ts: str, tool_msg: dict
) -> None:
    """Mark the thread as a Google Ads thread so follow-ups keep the account."""
    customer_id = tool_msg.get("resolved_customer_id")
    if not customer_id:
        return
    try:
        existing = await session_store.get_command_state(
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            command_name=ADS_QA_SESSION,
        ) or {}
        campaign_name = (
            tool_msg.get("resolved_campaign_name")
            or existing.get("last_campaign_name")
            or ""
        )
        await session_store.save_command_state(
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            command_name=ADS_QA_SESSION,
            state={
                "customer_id": customer_id,
                "account_name": tool_msg.get("resolved_account_name") or "",
                "last_campaign_name": campaign_name,
            },
            ttl_minutes=24 * 60,
        )
    except Exception as exc:
        logger.warning(f"ads_qa sticky state write failed: {exc}")


# ── Core agent turn ────────────────────────────────────────────────────────


async def _run_agent_reply(
    *,
    client: AsyncWebClient,
    user_id: int,
    real_name: str | None,
    email: str | None,
    tz: str,
    workday_start: str,
    workday_end: str,
    user_notes: str | None,
    slack_user_id: str,
    slack_team_id: str,
    channel_id: str,
    thread_ts: str,
    text: str,
    extra_context: str | None,
    react_ts: str | None = None,
) -> None:
    """Run one agent turn and post the streamed reply + any tool output / cards.

    Shared by the DM message handler and the campaign drill-down button.
    """
    if react_ts:
        _create_background_task(_react_eyes(client, channel_id, react_ts))

    try:
        loading = await client.chat_postMessage(
            channel=channel_id, thread_ts=thread_ts, text="🌀 Thinking…"
        )
        loading_ts = loading["ts"]
    except Exception:
        logger.exception("Failed to post loading placeholder")
        loading_ts = None

    history = await session_store.load_agent_history(
        user_id=user_id, channel_id=channel_id, thread_ts=thread_ts
    )
    logger.info(f"  → agent run with {len(history)} prior messages in history")

    updater: SlackStreamUpdater | None = None
    on_chunk = None
    on_tool_start = None
    if loading_ts:
        updater = SlackStreamUpdater(client, channel_id, loading_ts)
        on_chunk = updater.push

        async def _tool_start(tool_name: str) -> None:
            status = _TOOL_STATUS.get(tool_name)
            if status and updater is not None:
                await updater.status(status)

        on_tool_start = _tool_start

    pending_actions: list[dict] = []
    tool_messages: list[dict] = []
    try:
        reply, pending_actions, tool_messages = await run_agent_turn(
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
            on_tool_start=on_tool_start,
        )
    except Exception:
        # Full traceback is captured by Phoenix + Cloud Logging. The user-facing
        # message stays generic so we don't leak internals.
        logger.exception("agent turn failed")
        reply = (
            "⚠️ Something went wrong on my end. Try again in a moment — if it "
            "keeps failing, check the Phoenix dashboard for the trace."
        )

    # A tool already posted the substantive output (e.g. a Google Ads card) —
    # don't leave the placeholder showing an empty "(no response)".
    if tool_messages and reply.strip() in ("", "(no response)", "(empty)"):
        reply = "Here's what I found 👇"

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

    # Post tool-emitted output (Google Ads table + charts) and make the thread
    # sticky to the resolved account.
    for tool_msg in tool_messages:
        await _post_tool_message(client, channel_id, thread_ts, tool_msg)
        await _save_ads_thread_state(
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            tool_msg=tool_msg,
        )

    # If the agent proposed any writes, post an approval card per action.
    for action in pending_actions:
        primary_text, alt_text = _approval_button_labels(action)
        alternate = action.get("alternate")
        if alternate:
            blocks = await build_approval_blocks_with_alternates(
                user_id=user_id,
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
            blocks = await build_approval_blocks(
                [action], user_id=user_id, primary_button_text=primary_text
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

    tz, email, real_name = await _hydrate_user_profile(client, slack_user_id)
    user_id, workday_start, workday_end, user_notes = await _resolve_user(
        slack_team_id=slack_team_id,
        slack_user_id=slack_user_id,
        tz=tz,
        email=email,
        real_name=real_name,
    )
    if user_id is None:
        logger.error("user_id None after get_or_create_user")
        return

    # 1) Reply inside an active /wrike state-machine thread → route there.
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
            logger.info("  → non-scheduling /wrike reply; forwarding to agent")

    # 2) An established Google Ads thread → inject the sticky account.
    if extra_context is None and event.get("thread_ts"):
        ads_state = await session_store.get_command_state(
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            command_name=ADS_QA_SESSION,
        )
        if ads_state and ads_state.get("customer_id"):
            extra_context = _ads_thread_context(ads_state)
            logger.info("  → ads thread; injecting sticky account context")

    await _run_agent_reply(
        client=client,
        user_id=user_id,
        real_name=real_name,
        email=email,
        tz=tz,
        workday_start=workday_start,
        workday_end=workday_end,
        user_notes=user_notes,
        slack_user_id=slack_user_id,
        slack_team_id=slack_team_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        text=text,
        extra_context=extra_context,
        react_ts=msg_ts,
    )


async def _handle_ads_drill(body: dict, client: AsyncWebClient) -> None:
    """A campaign '🔍' button on an overview card — run a drill-down turn."""
    try:
        action = body["actions"][0]
        payload = json.loads(action["value"])
    except Exception as exc:
        logger.warning(f"ads drill: bad payload: {exc}")
        return

    customer_id = str(payload.get("customer_id") or "")
    campaign_id = str(payload.get("campaign_id") or "")
    campaign_name = str(payload.get("campaign_name") or "")
    if not campaign_id:
        return

    slack_user_id = body.get("user", {}).get("id") or ""
    slack_team_id = (
        body.get("team", {}).get("id") or body.get("user", {}).get("team_id") or ""
    )
    channel_id = body.get("channel", {}).get("id") or ""
    message = body.get("message", {}) or {}
    thread_ts = message.get("thread_ts") or message.get("ts")
    if not (slack_user_id and channel_id and thread_ts):
        return

    tz, email, real_name = await _hydrate_user_profile(client, slack_user_id)
    user_id, workday_start, workday_end, user_notes = await _resolve_user(
        slack_team_id=slack_team_id,
        slack_user_id=slack_user_id,
        tz=tz,
        email=email,
        real_name=real_name,
    )
    if user_id is None:
        return

    synthetic = (
        f'Drill into the Google Ads campaign "{campaign_name}" '
        f"(campaign_id {campaign_id}) — give me the detailed breakdown."
    )
    extra_context = _ads_thread_context(
        {"customer_id": customer_id, "account_name": ""}
    )
    await _run_agent_reply(
        client=client,
        user_id=user_id,
        real_name=real_name,
        email=email,
        tz=tz,
        workday_start=workday_start,
        workday_end=workday_end,
        user_notes=user_notes,
        slack_user_id=slack_user_id,
        slack_team_id=slack_team_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        text=synthetic,
        extra_context=extra_context,
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

    @app.action(re.compile(rf"^{ADS_DRILL_ACTION}:"))
    async def on_ads_drill(ack, body, client):
        await ack()
        await _handle_ads_drill(body, client)
