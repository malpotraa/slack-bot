"""User upsert + lookup helpers."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.db.models import User


async def get_or_create_user(
    session: AsyncSession,
    *,
    slack_team_id: str,
    slack_user_id: str,
    email: str | None = None,
    real_name: str | None = None,
    tz: str | None = None,
) -> User:
    stmt = select(User).where(
        User.slack_team_id == slack_team_id,
        User.slack_user_id == slack_user_id,
    )
    user = (await session.exec(stmt)).first()
    if user is None:
        user = User(
            slack_team_id=slack_team_id,
            slack_user_id=slack_user_id,
            email=email,
            real_name=real_name,
            tz=tz,
        )
        session.add(user)
        await session.flush()
        return user

    changed = False
    if email and user.email != email:
        user.email = email
        changed = True
    if real_name and user.real_name != real_name:
        user.real_name = real_name
        changed = True
    if tz and user.tz != tz:
        user.tz = tz
        changed = True
    if changed:
        user.updated_at = datetime.now(UTC)
        session.add(user)
    return user


async def get_user_by_slack_id(
    session: AsyncSession, slack_team_id: str, slack_user_id: str
) -> User | None:
    stmt = select(User).where(
        User.slack_team_id == slack_team_id,
        User.slack_user_id == slack_user_id,
    )
    return (await session.exec(stmt)).first()


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)
