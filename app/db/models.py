"""SQLModel tables. One row per user identity, one row per integration token.

Note on datetime columns:
  Every timestamp uses `sa_type=DateTime(timezone=True)` so it maps to
  Postgres `TIMESTAMP WITH TIME ZONE` and accepts tz-aware datetimes (which
  the rest of the code uses everywhere). SQLite ignores the timezone hint
  and stores naive UTC, which is fine — read paths handle both.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


_TS = DateTime(timezone=True)


class User(SQLModel, table=True):
    """A single Slack user across one workspace."""

    id: int | None = Field(default=None, primary_key=True)
    slack_team_id: str = Field(index=True)
    slack_user_id: str = Field(index=True)
    email: str | None = None
    real_name: str | None = None
    tz: str | None = None  # e.g. "America/Los_Angeles"
    workday_start: str = "09:00"
    workday_end: str = "18:00"
    # Tracks the most recent /connect card we posted so we can clean it up
    # once all three integrations are connected.
    connect_card_channel_id: str | None = None
    connect_card_ts: str | None = None
    created_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)
    updated_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)

    __table_args__ = ({"sqlite_autoincrement": True},)


class GoogleToken(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", unique=True, index=True)
    encrypted_refresh_token: str
    encrypted_access_token: str | None = None
    access_token_expires_at: datetime | None = Field(default=None, sa_type=_TS)
    scopes: str = ""
    google_email: str | None = None
    created_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)
    updated_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)


class WrikeToken(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", unique=True, index=True)
    encrypted_refresh_token: str
    encrypted_access_token: str
    access_token_expires_at: datetime = Field(sa_type=_TS)
    api_host: str = "www.wrike.com"
    wrike_contact_id: str | None = None
    created_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)
    updated_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)


class SlackUserToken(SQLModel, table=True):
    """User-token (xoxp-) for search:read on this user's behalf."""

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", unique=True, index=True)
    encrypted_user_token: str
    scopes: str = ""
    created_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)
    updated_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)


class ConversationSession(SQLModel, table=True):
    """Per-thread state: agent memory + /wrike state machine."""

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    channel_id: str = Field(index=True)
    thread_ts: str = Field(index=True)
    agent_history_json: str | None = None
    command_name: str | None = None
    command_state_json: str | None = None
    created_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)
    updated_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)
    expires_at: datetime | None = Field(default=None, sa_type=_TS)

    __table_args__ = ({"sqlite_autoincrement": True},)


class WorkflowStatusCache(SQLModel, table=True):
    """Cache of Wrike custom-status name → id per (user, workflow). Refreshed on demand."""

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    status_name: str = Field(index=True)
    custom_status_id: str
    workflow_id: str
    cached_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)
