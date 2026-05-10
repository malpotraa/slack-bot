"""Signed OAuth state tokens — carry slack_user_id + provider through the redirect dance.

We use itsdangerous to sign the payload so an attacker can't forge a callback that
links a malicious provider account to someone else's Slack user.
"""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

_SALT = "oauth-state-v1"
_MAX_AGE_SECONDS = 600  # 10 min — user must complete the OAuth dance in that window


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key=settings.app_secret_key, salt=_SALT)


def make_state(*, provider: str, slack_team_id: str, slack_user_id: str) -> str:
    return _serializer().dumps(
        {"provider": provider, "team": slack_team_id, "user": slack_user_id}
    )


def parse_state(token: str) -> dict[str, str]:
    try:
        return _serializer().loads(token, max_age=_MAX_AGE_SECONDS)
    except SignatureExpired as exc:
        raise ValueError("OAuth state expired — start over with /connect") from exc
    except BadSignature as exc:
        raise ValueError("Invalid OAuth state — possible CSRF") from exc
