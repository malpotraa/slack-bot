"""SQLModel tables. One row per user identity, one row per integration token.

Note on datetime columns:
  Every timestamp uses `sa_type=DateTime(timezone=True)` so it maps to
  Postgres `TIMESTAMP WITH TIME ZONE` and accepts tz-aware datetimes (which
  the rest of the code uses everywhere). SQLite ignores the timezone hint
  and stores naive UTC, which is fine — read paths handle both.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, UniqueConstraint
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
    # Opt-in daily /goodmorning briefing. Off by default — a user enables it
    # for themselves via `/goodmorning subscribe` or the button on the briefing.
    # `daily_briefing_time` is local HH:MM (24h) interpreted in the user's `tz`.
    daily_briefing_enabled: bool = False
    daily_briefing_time: str = "08:00"
    # Free-form user preferences (e.g. "I prefer 30-min focus blocks",
    # "always schedule on Wednesdays") that the assistant should keep in
    # mind across conversations. Edited via update_user_notes / cleared
    # via the same tool with an empty string.
    notes: str | None = None
    # Tracks the most recent /connect card we posted so we can clean it up
    # once all required integrations are connected.
    connect_card_channel_id: str | None = None
    connect_card_ts: str | None = None
    google_ads_connect_channel_id: str | None = None
    google_ads_connect_ts: str | None = None
    created_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)
    updated_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)

    __table_args__ = (
        UniqueConstraint("slack_team_id", "slack_user_id", name="uq_user_slack_identity"),
        {"sqlite_autoincrement": True},
    )


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


class GoogleAdsToken(SQLModel, table=True):
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

    __table_args__ = (
        UniqueConstraint("user_id", "channel_id", "thread_ts", name="uq_session_thread"),
        {"sqlite_autoincrement": True},
    )


class ApprovalExecution(SQLModel, table=True):
    """One-time claim for a Slack approval-card message."""

    id: int | None = Field(default=None, primary_key=True)
    approval_key: str = Field(unique=True, index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    slack_team_id: str = Field(index=True)
    slack_user_id: str = Field(index=True)
    channel_id: str = Field(index=True)
    message_ts: str = Field(index=True)
    action_id: str
    tool_name: str
    status: str = "claimed"
    error: str | None = None
    created_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)
    updated_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)

    __table_args__ = ({"sqlite_autoincrement": True},)


class ApprovalRequest(SQLModel, table=True):
    """Server-side payload for one approval button.

    Slack button values carry only the random token. The executable tool args
    stay in this table so they are not exposed to Slack clients.
    """

    id: int | None = Field(default=None, primary_key=True)
    token: str = Field(unique=True, index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    action_id: str = Field(index=True)
    tool_name: str = Field(index=True)
    args_json: str
    summary: str = ""
    status: str = "pending"
    created_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)
    updated_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)
    expires_at: datetime = Field(sa_type=_TS)

    __table_args__ = ({"sqlite_autoincrement": True},)


class WorkflowStatusCache(SQLModel, table=True):
    """Cache of Wrike custom-status name → id per (user, workflow). Refreshed on demand."""

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    status_name: str = Field(index=True)
    custom_status_id: str
    workflow_id: str
    cached_at: datetime = Field(default_factory=_utcnow, sa_type=_TS)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "status_name",
            "custom_status_id",
            name="uq_workflow_status_cache_entry",
        ),
    )
