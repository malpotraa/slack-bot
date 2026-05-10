"""Slack per-user OAuth: requests an xoxp- user token with `search:read`.

This is separate from the bot token. It lets the assistant call `search.messages`
on the user's behalf for /goodmorning's unreplied-mentions logic.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
from loguru import logger

from app.config import settings

AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
TOKEN_URL = "https://slack.com/api/oauth.v2.access"

# We only need user-level scopes here. Bot scopes are configured in the Slack app
# manifest itself, not requested via this URL.
USER_SCOPES = [
    "search:read",
    "channels:history",
    "groups:history",
    "im:history",
    "mpim:history",
    "users:read",
]


def authorize_url(state: str) -> str:
    params = {
        "client_id": settings.slack_client_id,
        "user_scope": ",".join(USER_SCOPES),
        "redirect_uri": settings.slack_user_redirect_uri,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.slack_client_id,
                "client_secret": settings.slack_client_secret,
                "code": code,
                "redirect_uri": settings.slack_user_redirect_uri,
            },
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            logger.error(f"Slack OAuth exchange not ok: {body}")
            raise RuntimeError(f"Slack OAuth failed: {body.get('error', 'unknown')}")
        return body
