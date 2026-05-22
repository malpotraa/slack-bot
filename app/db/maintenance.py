"""Small best-effort database cleanup tasks run at startup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import delete

from app.db.engine import session_scope
from app.db.models import ApprovalExecution, ApprovalRequest, ConversationSession


async def cleanup_expired_rows() -> None:
    """Purge expired sessions and old approval audit rows.

    This is intentionally best-effort and small: Cloud Run is single-instance
    for Socket Mode, so startup is a reasonable cadence at current scale.
    """
    now = datetime.now(UTC)
    approval_cutoff = now - timedelta(days=90)
    # Conversation history (agent threads) has no expiry of its own — retain it
    # for 60 days, then purge so old user prompts don't linger indefinitely.
    history_cutoff = now - timedelta(days=60)
    try:
        async with session_scope() as session:
            await session.execute(
                delete(ConversationSession).where(
                    ConversationSession.expires_at.is_not(None),
                    ConversationSession.expires_at < now,
                )
            )
            await session.execute(
                delete(ConversationSession).where(
                    ConversationSession.updated_at < history_cutoff
                )
            )
            await session.execute(
                delete(ApprovalExecution).where(
                    ApprovalExecution.created_at < approval_cutoff
                )
            )
            await session.execute(
                delete(ApprovalRequest).where(ApprovalRequest.expires_at < now)
            )
    except Exception as exc:
        logger.warning(f"database cleanup skipped: {exc}")
