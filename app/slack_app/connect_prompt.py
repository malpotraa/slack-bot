"""Shared 'connect this integration' prompt — one consistent card across commands.

`/wrike`, `/kpi`, and `/goodmorning` all need to tell a user that an
integration isn't connected. They used to do it three different ways (plain
text, a button card, a context note). This module gives them a single
button-card shape so the connect experience is identical everywhere.

The button `action_id`s match the no-op handlers registered in
`app.slack_app.commands.connect`, so Slack doesn't log "Unhandled request".
"""

from __future__ import annotations

from app.oauth import google as google_oauth
from app.oauth import google_ads as google_ads_oauth
from app.oauth import slack_user as slack_oauth
from app.oauth import wrike as wrike_oauth
from app.oauth.state import make_state

# provider key → (display label, authorize_url builder, button action_id)
_PROVIDERS: dict[str, tuple] = {
    "google": ("Google Calendar", google_oauth.authorize_url, "connect_google_calendar"),
    "google_ads": ("Google Ads", google_ads_oauth.authorize_url, "connect_google_ads"),
    "wrike": ("Wrike", wrike_oauth.authorize_url, "connect_wrike"),
    "slack_user": ("Slack search", slack_oauth.authorize_url, "connect_slack_search"),
}


def provider_label(provider: str) -> str:
    return _PROVIDERS[provider][0]


def connect_button(provider: str, *, slack_team_id: str, slack_user_id: str) -> dict:
    """A Block Kit button that starts the OAuth flow for one provider."""
    label, authorize_url, action_id = _PROVIDERS[provider]
    state = make_state(
        provider=provider, slack_team_id=slack_team_id, slack_user_id=slack_user_id
    )
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": f"Connect {label}"},
        "url": authorize_url(state),
        "style": "primary",
        "action_id": action_id,
    }


def connect_prompt_blocks(
    provider: str, *, slack_team_id: str, slack_user_id: str, reason: str
) -> list[dict]:
    """A one-section card: explanatory text + a single connect button."""
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": reason},
            "accessory": connect_button(
                provider, slack_team_id=slack_team_id, slack_user_id=slack_user_id
            ),
        }
    ]
