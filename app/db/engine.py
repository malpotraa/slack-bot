"""Async SQLAlchemy engine + session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from loguru import logger
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
# SQLModel's AsyncSession adds the `.exec()` method that our repos use.
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings

# Importing models registers them with SQLModel.metadata.
from app.db import models  # noqa: F401

_engine = create_async_engine(settings.database_url, echo=False, future=True)
_SessionFactory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Create tables if missing. Safe to call on every startup."""
    async with _engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    logger.info(f"DB ready at {settings.database_url}")


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Yields a session and commits on success, rolls back on exception."""
    session = _SessionFactory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def session_factory() -> async_sessionmaker[AsyncSession]:
    return _SessionFactory


async def dispose() -> None:
    await _engine.dispose()
