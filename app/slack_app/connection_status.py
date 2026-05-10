"""Aggregate which integrations a Slack user has already connected."""

from __future__ import annotations

from dataclasses import dataclass

from app.db import tokens as token_repo
from app.db.engine import session_scope
from app.db.users import get_user_by_slack_id


@dataclass
class ConnectionStatus:
    user_id: int | None
    google: bool
    wrike: bool
    slack_user_token: bool

    @property
    def all_connected(self) -> bool:
        return self.google and self.wrike and self.slack_user_token


async def status_for(slack_team_id: str, slack_user_id: str) -> ConnectionStatus:
    async with session_scope() as session:
        user = await get_user_by_slack_id(session, slack_team_id, slack_user_id)
        if user is None or user.id is None:
            return ConnectionStatus(None, False, False, False)
        google = await token_repo.get_google_token(session, user.id)
        wrike = await token_repo.get_wrike_token(session, user.id)
        slack_tok = await token_repo.get_slack_user_token(session, user.id)
    return ConnectionStatus(
        user_id=user.id,
        google=google is not None,
        wrike=wrike is not None,
        slack_user_token=slack_tok is not None,
    )
