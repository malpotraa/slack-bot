"""FastAPI app that hosts OAuth callback endpoints + a tiny status page."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from html import escape

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from loguru import logger
from slack_sdk.web.async_client import AsyncWebClient

from app.config import settings
from app.db import tokens as token_repo
from app.db.engine import session_scope
from app.db.users import get_or_create_user
from app.integrations import google_calendar as gcal
from app.oauth import google as google_oauth
from app.oauth import google_ads as google_ads_oauth
from app.oauth import slack_user as slack_oauth
from app.oauth import wrike as wrike_oauth
from app.oauth.state import parse_state
from app.slack_app.connection_status import status_for

_BACKGROUND_TASKS: set[asyncio.Task] = set()
_GENERIC_CONNECT_ERROR = "Connection failed. Please return to Slack and run /connect again."


def _track_background_task(task: asyncio.Task) -> None:
    _BACKGROUND_TASKS.add(task)

    def _done(done: asyncio.Task) -> None:
        _BACKGROUND_TASKS.discard(done)
        try:
            done.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(f"OAuth background task failed: {exc}")

    task.add_done_callback(_done)


async def _maybe_clean_up_connect_card(slack_team_id: str, slack_user_id: str) -> None:
    """If the user has connected the core integrations, update + delete the card.

    Run as a fire-and-forget task — the OAuth callback returns its 'Connected'
    page immediately and this cleanup happens in the background.
    """
    status = await status_for(slack_team_id, slack_user_id)
    if not status.core_connected:
        return

    # Find the saved connect-card location.
    async with session_scope() as session:
        from app.db.users import get_user_by_slack_id

        user = await get_user_by_slack_id(session, slack_team_id, slack_user_id)
        if user is None:
            return
        channel_id = user.connect_card_channel_id
        ts = user.connect_card_ts
        if not channel_id or not ts:
            return
        # Clear immediately so concurrent callbacks don't try to do this twice.
        user.connect_card_channel_id = None
        user.connect_card_ts = None
        session.add(user)

    client = AsyncWebClient(token=settings.slack_bot_token)
    try:
        if status.google_ads:
            text = (
                "✅ *All set!* Google Calendar, Google Ads, Wrike, and Slack search "
                "are connected.\n\nThis message will tidy itself up in a moment."
            )
        else:
            text = (
                "✅ *Core integrations connected.* Google Calendar, Wrike, and Slack search "
                "are ready.\n\nRun `/connect` again later if you want Google Ads for `/kpi`. "
                "This message will tidy itself up in a moment."
            )
        await client.chat_update(
            channel=channel_id,
            ts=ts,
            text="✅ Core integrations connected.",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": text,
                    },
                }
            ],
        )
    except Exception as exc:
        logger.warning(f"connect-card update failed: {exc}")
        return

    # Brief delay so the user sees the confirmation, then delete.
    await asyncio.sleep(20)
    try:
        await client.chat_delete(channel=channel_id, ts=ts)
        logger.info(f"deleted connect card for {slack_user_id}")
    except Exception as exc:
        logger.warning(f"connect-card delete failed: {exc}")


async def _maybe_clean_up_google_ads_prompt(slack_team_id: str, slack_user_id: str) -> None:
    """Update and delete the one-off /kpi Google Ads connect prompt."""
    async with session_scope() as session:
        from app.db.users import get_user_by_slack_id

        user = await get_user_by_slack_id(session, slack_team_id, slack_user_id)
        if user is None:
            return
        channel_id = user.google_ads_connect_channel_id
        ts = user.google_ads_connect_ts
        if not channel_id or not ts:
            return
        user.google_ads_connect_channel_id = None
        user.google_ads_connect_ts = None
        session.add(user)

    client = AsyncWebClient(token=settings.slack_bot_token)
    try:
        await client.chat_update(
            channel=channel_id,
            ts=ts,
            text="✅ Google Ads connected.",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "✅ *Google Ads connected.* Run `/kpi google <account> cpa` "
                            "when you're ready.\n\nThis message will tidy itself up in a moment."
                        ),
                    },
                }
            ],
        )
    except Exception as exc:
        logger.warning(f"Google Ads connect prompt update failed: {exc}")
        return

    await asyncio.sleep(20)
    try:
        await client.chat_delete(channel=channel_id, ts=ts)
    except Exception as exc:
        logger.warning(f"Google Ads connect prompt delete failed: {exc}")


def _kickoff_cleanup(slack_team_id: str, slack_user_id: str) -> None:
    """Fire-and-forget wrapper so callback handlers don't await the 20s sleep."""
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_maybe_clean_up_connect_card(slack_team_id, slack_user_id))
        _track_background_task(task)
    except Exception as exc:  # pragma: no cover
        logger.warning(f"cleanup kickoff failed: {exc}")


def _kickoff_google_ads_prompt_cleanup(slack_team_id: str, slack_user_id: str) -> None:
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            _maybe_clean_up_google_ads_prompt(slack_team_id, slack_user_id)
        )
        _track_background_task(task)
    except Exception as exc:  # pragma: no cover
        logger.warning(f"Google Ads prompt cleanup kickoff failed: {exc}")


def _success_page(provider: str) -> HTMLResponse:
    provider_html = escape(provider)
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><title>Connected</title>
        <style>body{{font-family:-apple-system,sans-serif;max-width:440px;margin:80px auto;
        padding:24px;border:1px solid #e5e7eb;border-radius:12px;background:#f9fafb}}
        h1{{font-size:20px;margin:0 0 12px}}p{{color:#4b5563;line-height:1.5}}</style>
        </head><body><h1>✅ {provider_html} connected</h1>
        <p>You can close this tab and return to Slack.</p></body></html>"""
    )


def _error_page(provider: str, message: str) -> HTMLResponse:
    provider_html = escape(provider)
    message_html = escape(message)
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><title>Error</title>
        <style>body{{font-family:-apple-system,sans-serif;max-width:440px;margin:80px auto;
        padding:24px;border:1px solid #fca5a5;border-radius:12px;background:#fef2f2}}
        h1{{font-size:20px;margin:0 0 12px;color:#991b1b}}p{{color:#7f1d1d;line-height:1.5}}
        code{{background:#fff;padding:2px 6px;border-radius:4px}}</style>
        </head><body><h1>⚠️ {provider_html} connect failed</h1>
        <p>{message_html}</p><p>Run <code>/connect</code> in Slack to try again.</p>
        </body></html>""",
        status_code=400,
    )


def _validate_provider(payload: dict[str, str], expected: str) -> None:
    got = payload.get("provider")
    if got != expected:
        raise ValueError("OAuth state provider mismatch — start over with /connect")


def create_oauth_app() -> FastAPI:
    app = FastAPI(title="Slack Assistant OAuth", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "ts": datetime.now(UTC).isoformat()}

    @app.get("/oauth/google/callback")
    async def google_callback(
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
    ) -> HTMLResponse:
        if error:
            return _error_page("Google", f"Google returned error: {error}")
        if not code or not state:
            return _error_page("Google", "Missing code/state")
        try:
            payload = parse_state(state)
            _validate_provider(payload, "google")
        except ValueError as exc:
            return _error_page("Google", str(exc))

        try:
            tok = await google_oauth.exchange_code(code)
            access = tok.get("access_token")
            refresh = tok.get("refresh_token")  # only present on first consent
            scope = tok.get("scope", "")
            expires_at = google_oauth.expires_at_from_seconds(tok.get("expires_in"))
            email = None
            if access:
                try:
                    email = (await google_oauth.fetch_userinfo(access)).get("email")
                except Exception as exc:  # pragma: no cover
                    logger.warning(f"userinfo fetch failed: {exc}")

            async with session_scope() as session:
                user = await get_or_create_user(
                    session,
                    slack_team_id=payload["team"],
                    slack_user_id=payload["user"],
                )
                await token_repo.upsert_google_token(
                    session,
                    user_id=user.id,  # type: ignore[arg-type]
                    refresh_token=refresh,
                    access_token=access,
                    access_token_expires_at=expires_at,
                    scopes=scope,
                    google_email=email,
                )
                gcal.invalidate_user_cache(user.id)  # type: ignore[arg-type]
            logger.info(f"Google connected for slack user {payload['user']} ({email})")
            _kickoff_cleanup(payload["team"], payload["user"])
            return _success_page("Google Calendar")
        except Exception:  # pragma: no cover
            logger.exception("Google callback failed")
            return _error_page("Google", _GENERIC_CONNECT_ERROR)

    @app.get("/oauth/google-ads/callback")
    async def google_ads_callback(
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
    ) -> HTMLResponse:
        if error:
            return _error_page("Google Ads", f"Google returned error: {error}")
        if not code or not state:
            return _error_page("Google Ads", "Missing code/state")
        try:
            payload = parse_state(state)
            _validate_provider(payload, "google_ads")
        except ValueError as exc:
            return _error_page("Google Ads", str(exc))

        try:
            tok = await google_ads_oauth.exchange_code(code)
            access = tok.get("access_token")
            refresh = tok.get("refresh_token")
            scope = tok.get("scope", "")
            expires_at = google_ads_oauth.expires_at_from_seconds(tok.get("expires_in"))
            email = None
            if access:
                try:
                    email = (await google_ads_oauth.fetch_userinfo(access)).get("email")
                except Exception as exc:  # pragma: no cover
                    logger.warning(f"Google Ads userinfo fetch failed: {exc}")

            async with session_scope() as session:
                user = await get_or_create_user(
                    session,
                    slack_team_id=payload["team"],
                    slack_user_id=payload["user"],
                )
                await token_repo.upsert_google_ads_token(
                    session,
                    user_id=user.id,  # type: ignore[arg-type]
                    refresh_token=refresh,
                    access_token=access,
                    access_token_expires_at=expires_at,
                    scopes=scope,
                    google_email=email,
                )
            logger.info(f"Google Ads connected for slack user {payload['user']} ({email})")
            _kickoff_google_ads_prompt_cleanup(payload["team"], payload["user"])
            _kickoff_cleanup(payload["team"], payload["user"])
            return _success_page("Google Ads")
        except Exception:  # pragma: no cover
            logger.exception("Google Ads callback failed")
            return _error_page("Google Ads", _GENERIC_CONNECT_ERROR)

    @app.get("/oauth/wrike/callback")
    async def wrike_callback(
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
    ) -> HTMLResponse:
        if error:
            return _error_page("Wrike", f"Wrike returned error: {error}")
        if not code or not state:
            return _error_page("Wrike", "Missing code/state")
        try:
            payload = parse_state(state)
            _validate_provider(payload, "wrike")
        except ValueError as exc:
            return _error_page("Wrike", str(exc))

        try:
            tok = await wrike_oauth.exchange_code(code)
            access = tok["access_token"]
            refresh = tok["refresh_token"]
            host = tok.get("host", "www.wrike.com")
            expires_at = wrike_oauth.expires_at_from_seconds(tok.get("expires_in"))

            async with session_scope() as session:
                user = await get_or_create_user(
                    session,
                    slack_team_id=payload["team"],
                    slack_user_id=payload["user"],
                )
                await token_repo.upsert_wrike_token(
                    session,
                    user_id=user.id,  # type: ignore[arg-type]
                    refresh_token=refresh,
                    access_token=access,
                    access_token_expires_at=expires_at,
                    api_host=host,
                )
            logger.info(f"Wrike connected for slack user {payload['user']} (host={host})")
            _kickoff_cleanup(payload["team"], payload["user"])
            return _success_page("Wrike")
        except HTTPException:
            raise
        except Exception:
            logger.exception("Wrike callback failed")
            return _error_page("Wrike", _GENERIC_CONNECT_ERROR)

    @app.get("/oauth/slack/callback")
    async def slack_callback(
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
    ) -> HTMLResponse:
        if error:
            return _error_page("Slack", f"Slack returned error: {error}")
        if not code or not state:
            return _error_page("Slack", "Missing code/state")
        try:
            payload = parse_state(state)
            _validate_provider(payload, "slack_user")
        except ValueError as exc:
            return _error_page("Slack", str(exc))

        try:
            body = await slack_oauth.exchange_code(code)
            authed_user = body.get("authed_user", {})
            authed_user_id = authed_user.get("id")
            authed_team_id = (body.get("team") or {}).get("id")
            if authed_user_id and authed_user_id != payload["user"]:
                return _error_page("Slack", "Slack user mismatch — start over with /connect")
            if authed_team_id and authed_team_id != payload["team"]:
                return _error_page("Slack", "Slack workspace mismatch — start over with /connect")
            user_token = authed_user.get("access_token")
            scopes = authed_user.get("scope", "")
            if not user_token:
                return _error_page("Slack", "No user token returned")

            async with session_scope() as session:
                user = await get_or_create_user(
                    session,
                    slack_team_id=payload["team"],
                    slack_user_id=payload["user"],
                )
                await token_repo.upsert_slack_user_token(
                    session,
                    user_id=user.id,  # type: ignore[arg-type]
                    user_token=user_token,
                    scopes=scopes,
                )
            logger.info(f"Slack user-token connected for {payload['user']}")
            _kickoff_cleanup(payload["team"], payload["user"])
            return _success_page("Slack search")
        except Exception:
            logger.exception("Slack callback failed")
            return _error_page("Slack", _GENERIC_CONNECT_ERROR)

    return app
