"""Google Ads OAuth: build authorize URL, exchange code, refresh tokens."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from loguru import logger

from app.config import settings
from app.oauth._logging import safe_error_summary
from app.utils.http_client import shared_async_client

GOOGLE_ADS_SCOPES = [
    "https://www.googleapis.com/auth/adwords",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def authorize_url(state: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_ads_redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_ADS_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    resp = await shared_async_client(timeout=20).post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_ads_redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    if resp.status_code != 200:
        logger.error(f"Google Ads code exchange failed: {safe_error_summary(resp)}")
    resp.raise_for_status()
    return resp.json()


async def refresh_access_token(refresh_token: str) -> dict:
    resp = await shared_async_client(timeout=20).post(
        TOKEN_URL,
        data={
            "refresh_token": refresh_token,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "grant_type": "refresh_token",
        },
    )
    if resp.status_code != 200:
        logger.error(f"Google Ads refresh failed: {safe_error_summary(resp)}")
    resp.raise_for_status()
    return resp.json()


async def fetch_userinfo(access_token: str) -> dict:
    resp = await shared_async_client(timeout=20).get(
        USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
    )
    resp.raise_for_status()
    return resp.json()


def expires_at_from_seconds(expires_in: int | None) -> datetime:
    seconds = expires_in or 3600
    return datetime.now(UTC) + timedelta(seconds=max(seconds - 60, 60))
