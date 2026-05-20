"""Read/write helpers for the three token tables. All values are encrypted at rest."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.db.crypto import cipher
from app.db.models import GoogleAdsToken, GoogleToken, SlackUserToken, WrikeToken

# ── Google ─────────────────────────────────────────────────────────────────


async def upsert_google_token(
    session: AsyncSession,
    *,
    user_id: int,
    refresh_token: str | None,
    access_token: str | None,
    access_token_expires_at: datetime | None,
    scopes: str,
    google_email: str | None,
) -> GoogleToken:
    existing = (
        await session.exec(select(GoogleToken).where(GoogleToken.user_id == user_id))
    ).first()
    c = cipher()
    now = datetime.now(UTC)

    if existing is None:
        if not refresh_token:
            raise ValueError("First-time Google connect requires a refresh_token")
        existing = GoogleToken(
            user_id=user_id,
            encrypted_refresh_token=c.encrypt(refresh_token),
            encrypted_access_token=c.encrypt(access_token) if access_token else None,
            access_token_expires_at=access_token_expires_at,
            scopes=scopes,
            google_email=google_email,
        )
        session.add(existing)
        return existing

    if refresh_token:
        existing.encrypted_refresh_token = c.encrypt(refresh_token)
    if access_token:
        existing.encrypted_access_token = c.encrypt(access_token)
    if access_token_expires_at:
        existing.access_token_expires_at = access_token_expires_at
    if scopes:
        existing.scopes = scopes
    if google_email:
        existing.google_email = google_email
    existing.updated_at = now
    session.add(existing)
    return existing


async def get_google_token(session: AsyncSession, user_id: int) -> GoogleToken | None:
    return (
        await session.exec(select(GoogleToken).where(GoogleToken.user_id == user_id))
    ).first()


def decrypt_google(token: GoogleToken) -> tuple[str, str | None]:
    c = cipher()
    refresh = c.decrypt(token.encrypted_refresh_token)
    access = c.decrypt(token.encrypted_access_token) if token.encrypted_access_token else None
    return refresh, access


# ── Google Ads ─────────────────────────────────────────────────────────────


async def upsert_google_ads_token(
    session: AsyncSession,
    *,
    user_id: int,
    refresh_token: str | None,
    access_token: str | None,
    access_token_expires_at: datetime | None,
    scopes: str,
    google_email: str | None,
) -> GoogleAdsToken:
    existing = (
        await session.exec(select(GoogleAdsToken).where(GoogleAdsToken.user_id == user_id))
    ).first()
    c = cipher()
    now = datetime.now(UTC)

    if existing is None:
        if not refresh_token:
            raise ValueError("First-time Google Ads connect requires a refresh_token")
        existing = GoogleAdsToken(
            user_id=user_id,
            encrypted_refresh_token=c.encrypt(refresh_token),
            encrypted_access_token=c.encrypt(access_token) if access_token else None,
            access_token_expires_at=access_token_expires_at,
            scopes=scopes,
            google_email=google_email,
        )
        session.add(existing)
        return existing

    if refresh_token:
        existing.encrypted_refresh_token = c.encrypt(refresh_token)
    if access_token:
        existing.encrypted_access_token = c.encrypt(access_token)
    if access_token_expires_at:
        existing.access_token_expires_at = access_token_expires_at
    if scopes:
        existing.scopes = scopes
    if google_email:
        existing.google_email = google_email
    existing.updated_at = now
    session.add(existing)
    return existing


async def get_google_ads_token(
    session: AsyncSession, user_id: int
) -> GoogleAdsToken | None:
    return (
        await session.exec(select(GoogleAdsToken).where(GoogleAdsToken.user_id == user_id))
    ).first()


def decrypt_google_ads(token: GoogleAdsToken) -> tuple[str, str | None]:
    c = cipher()
    refresh = c.decrypt(token.encrypted_refresh_token)
    access = c.decrypt(token.encrypted_access_token) if token.encrypted_access_token else None
    return refresh, access


# ── Wrike ──────────────────────────────────────────────────────────────────


async def upsert_wrike_token(
    session: AsyncSession,
    *,
    user_id: int,
    refresh_token: str,
    access_token: str,
    access_token_expires_at: datetime,
    api_host: str,
) -> WrikeToken:
    existing = (
        await session.exec(select(WrikeToken).where(WrikeToken.user_id == user_id))
    ).first()
    c = cipher()
    if existing is None:
        existing = WrikeToken(
            user_id=user_id,
            encrypted_refresh_token=c.encrypt(refresh_token),
            encrypted_access_token=c.encrypt(access_token),
            access_token_expires_at=access_token_expires_at,
            api_host=api_host,
        )
    else:
        existing.encrypted_refresh_token = c.encrypt(refresh_token)
        existing.encrypted_access_token = c.encrypt(access_token)
        existing.access_token_expires_at = access_token_expires_at
        existing.api_host = api_host
        existing.updated_at = datetime.now(UTC)
    session.add(existing)
    return existing


async def get_wrike_token(session: AsyncSession, user_id: int) -> WrikeToken | None:
    return (
        await session.exec(select(WrikeToken).where(WrikeToken.user_id == user_id))
    ).first()


def decrypt_wrike(token: WrikeToken) -> tuple[str, str]:
    c = cipher()
    return c.decrypt(token.encrypted_refresh_token), c.decrypt(token.encrypted_access_token)


# ── Slack user-token (xoxp) ────────────────────────────────────────────────


async def upsert_slack_user_token(
    session: AsyncSession,
    *,
    user_id: int,
    user_token: str,
    scopes: str,
) -> SlackUserToken:
    existing = (
        await session.exec(select(SlackUserToken).where(SlackUserToken.user_id == user_id))
    ).first()
    c = cipher()
    if existing is None:
        existing = SlackUserToken(
            user_id=user_id,
            encrypted_user_token=c.encrypt(user_token),
            scopes=scopes,
        )
    else:
        existing.encrypted_user_token = c.encrypt(user_token)
        existing.scopes = scopes
        existing.updated_at = datetime.now(UTC)
    session.add(existing)
    return existing


async def get_slack_user_token(session: AsyncSession, user_id: int) -> SlackUserToken | None:
    return (
        await session.exec(select(SlackUserToken).where(SlackUserToken.user_id == user_id))
    ).first()


def decrypt_slack_user_token(token: SlackUserToken) -> str:
    return cipher().decrypt(token.encrypted_user_token)
