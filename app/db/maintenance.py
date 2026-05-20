"""Small best-effort database cleanup tasks run at startup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import delete

from app.db.engine import session_scope
from app.db.models import ApprovalExecution, ConversationSession


async def cleanup_expired_rows() -> None:
    """Purge expired sessions and old approval audit rows.

    This is intentionally best-effort and small: Cloud Run is single-instance
    for Socket Mode, so startup is a reasonable cadence at current scale.
    """
    now = datetime.now(UTC)
    approval_cutoff = now - timedelta(days=90)
    try:
        async with session_scope() as session:
            await session.execute(
                delete(ConversationSession).where(
                    ConversationSession.expires_at.is_not(None),
                    ConversationSession.expires_at < now,
                )
            )
            await session.execute(
                delete(ApprovalExecution).where(
                    ApprovalExecution.created_at < approval_cutoff
                )
            )
    except Exception as exc:
        logger.warning(f"database cleanup skipped: {exc}")
