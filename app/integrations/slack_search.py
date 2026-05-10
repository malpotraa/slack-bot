"""Find Slack mentions the user hasn't replied to.

Uses the user's xoxp- token (search:read) to query `search.messages`, then
checks each match for a reply heuristic.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from app.db import tokens as token_repo
from app.db.engine import session_scope

REPLY_REACTIONS = {"white_check_mark", "+1", "thumbsup", "eyes", "ok_hand", "pray"}


class SlackUserNotConnectedError(RuntimeError):
    pass


@dataclass(frozen=True)
class UnrepliedMention:
    channel_id: str
    channel_name: str
    is_dm: bool
    author_id: str
    author_name: str
    text: str
    ts: str  # Slack timestamp (string of float)
    permalink: str
    posted_at: datetime  # tz-aware UTC


async def _user_token(user_id: int) -> str:
    async with session_scope() as session:
        tok = await token_repo.get_slack_user_token(session, user_id)
        if tok is None:
            raise SlackUserNotConnectedError("Slack user-token not connected. Run /connect.")
        return token_repo.decrypt_slack_user_token(tok)


def _ts_to_dt(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=__import__("datetime").timezone.utc)


async def find_unreplied_mentions(
    *,
    user_id: int,
    slack_user_id: str,
    days: int = 7,
    bot_token: str,
    max_results: int = 50,
) -> list[UnrepliedMention]:
    """Return mentions of `slack_user_id` in the last `days` days the user hasn't replied to.

    Reply heuristic: REPLIED if any of:
      - User posted any message in that thread
      - User reacted to the message (subset of reactions count as ack)
      - User authored the mention themselves (skip)
    """
    user_token = await _user_token(user_id)
    user_client = AsyncWebClient(token=user_token)
    bot_client = AsyncWebClient(token=bot_token)

    # Resolve bot identity. We collect three signals because Slack's
    # search.messages returns app-posted messages with inconsistent shape:
    # sometimes `bot_id` is set, sometimes only `username`, sometimes `user`
    # is the bot's user_id. We filter aggressively on all three.
    bot_user_id = ""
    bot_id = ""
    bot_display_name = ""
    try:
        auth = await bot_client.auth_test()
        bot_user_id = auth.get("user_id") or ""
        bot_id = auth.get("bot_id") or ""
    except SlackApiError:
        pass
    if bot_user_id:
        try:
            info = await bot_client.users_info(user=bot_user_id)
            u = info.get("user", {}) or {}
            profile = u.get("profile", {}) or {}
            bot_display_name = (
                profile.get("real_name")
                or profile.get("display_name")
                or u.get("real_name")
                or u.get("name")
                or ""
            ).strip().lower()
        except SlackApiError:
            pass

    logger.info(
        f"bot identity for filter: user_id={bot_user_id!r} bot_id={bot_id!r} name={bot_display_name!r}"
    )

    after = (
        (datetime.utcnow() - timedelta(days=days)).date().isoformat()
    )  # search uses YYYY-MM-DD
    query = f"<@{slack_user_id}> after:{after}"
    logger.info(f"Slack search query: {query!r}")

    try:
        search_resp = await user_client.search_messages(
            query=query, sort="timestamp", sort_dir="desc", count=max_results
        )
    except SlackApiError as exc:
        if exc.response.get("error") in ("not_authed", "invalid_auth", "token_revoked"):
            raise SlackUserNotConnectedError(
                "Slack user-token invalid; reconnect via /connect"
            ) from exc
        raise

    matches = (search_resp.get("messages", {}) or {}).get("matches", [])
    candidates: list[dict] = []
    skipped_bot = 0
    for m in matches:
        # Self-mentions
        if m.get("user") == slack_user_id:
            continue

        # Generic bot/app message detection
        if m.get("bot_id") or m.get("subtype") in ("bot_message", "app_message"):
            skipped_bot += 1
            continue

        # Our specific bot's user_id
        if bot_user_id and m.get("user") == bot_user_id:
            skipped_bot += 1
            continue

        # Our specific bot's display name (case-insensitive). search.messages
        # often sets `username` for app messages even when bot_id is missing.
        if bot_display_name:
            match_username = (m.get("username") or "").strip().lower()
            if match_username == bot_display_name:
                skipped_bot += 1
                continue

        candidates.append(m)

    if skipped_bot:
        logger.info(f"filtered {skipped_bot} bot/app messages from search results")

    # Concurrent reply-checks, lightly throttled.
    sem = asyncio.Semaphore(5)

    async def _is_replied(match: dict) -> bool:
        async with sem:
            channel = (match.get("channel") or {}).get("id")
            ts = match.get("ts")
            if not channel or not ts:
                return True

            # Did user post in the same thread?
            try:
                replies = await user_client.conversations_replies(
                    channel=channel, ts=ts, limit=200
                )
                for r in replies.get("messages", []):
                    if r.get("ts") == ts:
                        continue
                    if r.get("user") == slack_user_id:
                        return True
            except SlackApiError as exc:
                # Common: not_in_channel — fall through, can't determine
                if exc.response.get("error") not in ("not_in_channel", "channel_not_found"):
                    logger.warning(f"replies fetch failed: {exc.response.get('error')}")

            # Reactions on the message itself
            try:
                rxn = await bot_client.reactions_get(channel=channel, timestamp=ts)
                msg = (rxn.get("message") or {})
                for r in msg.get("reactions", []) or []:
                    if r.get("name") in REPLY_REACTIONS and slack_user_id in (
                        r.get("users") or []
                    ):
                        return True
            except SlackApiError:
                pass

            return False

    flags = await asyncio.gather(*(_is_replied(m) for m in candidates), return_exceptions=False)

    unreplied: list[UnrepliedMention] = []
    for match, replied in zip(candidates, flags, strict=False):
        if replied:
            continue
        chan = match.get("channel") or {}
        unreplied.append(
            UnrepliedMention(
                channel_id=chan.get("id", ""),
                channel_name=chan.get("name") or "(dm)",
                is_dm=chan.get("is_im", False) or chan.get("is_mpim", False),
                author_id=match.get("user", ""),
                author_name=match.get("username") or match.get("user", ""),
                text=match.get("text", ""),
                ts=match.get("ts", ""),
                permalink=match.get("permalink", ""),
                posted_at=_ts_to_dt(match.get("ts", "0")),
            )
        )
    return unreplied
