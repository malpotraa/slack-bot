"""Single-process entrypoint: runs Slack Bolt (Socket Mode) + FastAPI (OAuth callbacks)."""

from __future__ import annotations

import asyncio
import signal

import uvicorn
from cryptography.fernet import Fernet
from loguru import logger

from app.config import settings
from app.db.engine import dispose, init_db
from app.db.maintenance import cleanup_expired_rows
from app.logging_setup import configure_logging
from app.oauth.server import create_oauth_app
from app.observability import configure_observability
from app.slack_app.app import create_slack_app, run_socket_mode
from app.slack_app.scheduler import start_briefing_scheduler
from app.utils.http_client import close_shared_async_clients


async def _run_oauth_server() -> None:
    app = create_oauth_app()
    config = uvicorn.Config(
        app,
        host=settings.oauth_host,
        port=settings.oauth_port,
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    await server.serve()


async def _validate_startup() -> None:
    missing = []
    required = {
        "SLACK_BOT_TOKEN": settings.slack_bot_token,
        "SLACK_APP_TOKEN": settings.slack_app_token,
        "SLACK_SIGNING_SECRET": settings.slack_signing_secret,
        "ANTHROPIC_API_KEY": settings.anthropic_api_key,
        "TOKEN_ENCRYPTION_KEY": settings.token_encryption_key,
        "APP_SECRET_KEY": settings.app_secret_key,
        "APP_BASE_URL": settings.app_base_url,  # required so OAuth redirects resolve
        "DATABASE_URL": settings.database_url,
    }
    for name, val in required.items():
        if not val or (name == "APP_SECRET_KEY" and val.startswith("change-me")):
            missing.append(name)
    if missing:
        raise RuntimeError(
            "Missing required env vars: "
            + ", ".join(missing)
            + ". In Cloud Run these come from --update-secrets and --set-env-vars."
        )
    try:
        Fernet(settings.token_encryption_key.encode("utf-8"))
    except Exception as exc:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not a valid Fernet key.") from exc


async def amain() -> None:
    configure_logging()
    await _validate_startup()
    configure_observability()
    await init_db()
    await cleanup_expired_rows()

    slack_app = create_slack_app()
    scheduler = start_briefing_scheduler(slack_app.client)

    logger.info(f"Booting on {settings.app_env} | base_url={settings.app_base_url}")

    stop = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:  # Windows
            pass

    slack_task = asyncio.create_task(run_socket_mode(slack_app), name="slack-socket")
    http_task = asyncio.create_task(_run_oauth_server(), name="oauth-http")
    stop_task = asyncio.create_task(stop.wait(), name="shutdown-signal")

    done, pending = await asyncio.wait(
        {slack_task, http_task, stop_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    for t in pending:
        t.cancel()
    for t in done:
        if t.get_name() != "shutdown-signal" and t.exception():
            logger.error(f"Task {t.get_name()} crashed: {t.exception()}")

    scheduler.shutdown(wait=False)
    await dispose()
    await close_shared_async_clients()
    logger.info("Bye 👋")


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
