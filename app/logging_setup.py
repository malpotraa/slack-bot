"""Loguru configuration. Imported once from main."""

from __future__ import annotations

import logging
import sys

from loguru import logger

from app.config import settings


class _InterceptHandler(logging.Handler):
    """Route stdlib logging (slack-bolt, uvicorn, google-api) into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def configure_logging() -> None:
    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.log_level,
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> "
            "<level>{level: <8}</level> "
            "<cyan>{name}:{line}</cyan> "
            "<level>{message}</level>"
        ),
        backtrace=False,
        diagnose=False,
    )

    logging.basicConfig(handlers=[_InterceptHandler()], level=0, force=True)
    for noisy in ("uvicorn.access", "httpcore", "httpx", "googleapiclient.discovery_cache"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
