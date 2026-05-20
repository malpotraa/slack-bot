"""Slack Bolt app factory + Socket Mode handler."""

from __future__ import annotations

import os

from loguru import logger
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from app.config import settings
from app.slack_app import approval as approval_actions
from app.slack_app.commands import connect as connect_cmd
from app.slack_app.commands import goodmorning as goodmorning_cmd
from app.slack_app.commands import kpi_cmd
from app.slack_app.commands import wrike_cmd
from app.slack_app.handlers import register_message_handlers


def create_slack_app() -> AsyncApp:
    """Build a single-tenant AsyncApp.

    slack-bolt auto-enables an OAuth distribution flow (file-based
    InstallationStore + OAuthStateStore) when it sees SLACK_CLIENT_ID and
    SLACK_CLIENT_SECRET in the environment. That flow ignores the static bot
    token, looks for an Installation row that doesn't exist, and rejects every
    incoming request with "AuthorizeResult not found".

    We *do* set those env vars (the FastAPI OAuth server needs them via
    `settings.slack_client_id` for the per-user xoxp- token flow), but they
    must not be visible to AsyncApp's auto-detection. So we pop them while
    building the Bolt app, then restore them so anything else still works.
    """
    saved_env: dict[str, str] = {}
    for key in ("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET"):
        if key in os.environ:
            saved_env[key] = os.environ.pop(key)

    try:
        app = AsyncApp(
            token=settings.slack_bot_token,
            signing_secret=settings.slack_signing_secret,
        )
    finally:
        os.environ.update(saved_env)

    connect_cmd.register(app)
    goodmorning_cmd.register(app)
    kpi_cmd.register(app)
    wrike_cmd.register(app)
    approval_actions.register(app)
    register_message_handlers(app)

    @app.error
    async def on_error(error, body, logger=logger):
        logger.exception(f"Bolt error: {error} | body={body!r}")

    return app


async def run_socket_mode(app: AsyncApp) -> None:
    if not settings.slack_app_token:
        raise RuntimeError("SLACK_APP_TOKEN missing — required for Socket Mode")
    handler = AsyncSocketModeHandler(app, settings.slack_app_token)
    await handler.start_async()
