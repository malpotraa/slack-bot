"""Google OAuth: build authorize URL, exchange code, refresh tokens."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from loguru import logger

from app.config import settings

# Calendar read+write + userinfo for the connected email
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def authorize_url(state: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # ensures we get a refresh_token even on re-auth
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    """POST the auth code, return the full token response dict."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def refresh_access_token(refresh_token: str) -> dict:
    """Trade a refresh token for a fresh access token."""
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code != 200:
            logger.error(f"Google refresh failed {resp.status_code}: {resp.text}")
        resp.raise_for_status()
        return resp.json()


async def fetch_userinfo(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        resp.raise_for_status()
        return resp.json()


def expires_at_from_seconds(expires_in: int | None) -> datetime:
    seconds = expires_in or 3600
    # subtract 60s safety margin to avoid using just-expired tokens
    return datetime.now(UTC) + timedelta(seconds=max(seconds - 60, 60))
