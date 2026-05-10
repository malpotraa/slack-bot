"""Wrike OAuth 2.0: authorize, exchange, refresh.

Wrike returns a `host` field that tells us which API host to use for that user
(www.wrike.com / app-us2.wrike.com / app-eu.wrike.com / etc.). Always honor it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from loguru import logger

from app.config import settings

AUTHORIZE_URL = "https://login.wrike.com/oauth2/authorize/v4"
TOKEN_URL = "https://login.wrike.com/oauth2/token"


def authorize_url(state: str) -> str:
    params = {
        "client_id": settings.wrike_client_id,
        "response_type": "code",
        "redirect_uri": settings.wrike_redirect_uri,
        "state": state,
        # No scope param — Wrike OAuth grants the union of scopes configured on the app.
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.wrike_client_id,
                "client_secret": settings.wrike_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.wrike_redirect_uri,
            },
        )
        if resp.status_code != 200:
            logger.error(f"Wrike code exchange failed {resp.status_code}: {resp.text}")
        resp.raise_for_status()
        return resp.json()


async def refresh_access_token(refresh_token: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.wrike_client_id,
                "client_secret": settings.wrike_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        if resp.status_code != 200:
            logger.error(f"Wrike refresh failed {resp.status_code}: {resp.text}")
        resp.raise_for_status()
        return resp.json()


def expires_at_from_seconds(expires_in: int | None) -> datetime:
    seconds = expires_in or 3600
    return datetime.now(UTC) + timedelta(seconds=max(seconds - 60, 60))
