"""/connect — DMs the user with OAuth links and current connection status."""

from __future__ import annotations

from loguru import logger

from app.db.engine import session_scope
from app.db.users import get_or_create_user
from app.oauth import google as google_oauth
from app.oauth import google_ads as google_ads_oauth
from app.oauth import slack_user as slack_oauth
from app.oauth import wrike as wrike_oauth
from app.oauth.state import make_state
from app.slack_app.connection_status import status_for


def _build_blocks(
    *,
    google_url: str,
    google_ads_url: str,
    wrike_url: str,
    slack_url: str,
    google_done: bool,
    google_ads_done: bool,
    wrike_done: bool,
    slack_done: bool,
) -> list[dict]:
    def row(label: str, url: str, done: bool) -> dict:
        prefix = "✅" if done else "🔗"
        action = "Reconnect" if done else "Connect"
        return {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{prefix}  *{label}*"},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": action},
                "url": url,
                "style": "primary" if not done else None,
                "action_id": f"connect_{label.lower().replace(' ', '_')}",
            },
        }

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Connect your accounts"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "Authorize each integration the assistant uses. "
                    "Each link opens a browser tab; come back here when you're done."
                ),
            },
        },
        {"type": "divider"},
        row("Google Calendar", google_url, google_done),
        row("Google Ads", google_ads_url, google_ads_done),
        row("Wrike", wrike_url, wrike_done),
        row("Slack search", slack_url, slack_done),
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        "_Slack search lets the assistant find @-mentions across channels — "
                        "needed for `/goodmorning`. Google Ads powers `/kpi`._"
                    ),
                }
            ],
        },
    ]
    # Strip None style values (Slack rejects them).
    for b in blocks:
        acc = b.get("accessory")
        if acc and acc.get("style") is None:
            acc.pop("style", None)
    return blocks


def register(app):
    # The connect-card buttons carry a `url`, so Slack handles the navigation
    # itself — but it also notifies the app via block_actions. Register a no-op
    # handler so Bolt doesn't log "Unhandled request".
    @app.action("connect_google_calendar")
    async def _noop_google(ack):
        await ack()

    @app.action("connect_google_ads")
    async def _noop_google_ads(ack):
        await ack()

    @app.action("connect_wrike")
    async def _noop_wrike(ack):
        await ack()

    @app.action("connect_slack_search")
    async def _noop_slack(ack):
        await ack()

    @app.command("/connect")
    async def handle_connect(ack, body, client, logger=logger):
        await ack()
        slack_team_id = body["team_id"]
        slack_user_id = body["user_id"]

        # Ensure user row exists so the OAuth callbacks have something to link to.
        async with session_scope() as session:
            await get_or_create_user(
                session, slack_team_id=slack_team_id, slack_user_id=slack_user_id
            )

        google_state = make_state(
            provider="google", slack_team_id=slack_team_id, slack_user_id=slack_user_id
        )
        google_ads_state = make_state(
            provider="google_ads", slack_team_id=slack_team_id, slack_user_id=slack_user_id
        )
        wrike_state = make_state(
            provider="wrike", slack_team_id=slack_team_id, slack_user_id=slack_user_id
        )
        slack_state = make_state(
            provider="slack_user", slack_team_id=slack_team_id, slack_user_id=slack_user_id
        )

        google_url = google_oauth.authorize_url(google_state)
        google_ads_url = google_ads_oauth.authorize_url(google_ads_state)
        wrike_url = wrike_oauth.authorize_url(wrike_state)
        slack_url = slack_oauth.authorize_url(slack_state)

        status = await status_for(slack_team_id, slack_user_id)
        blocks = _build_blocks(
            google_url=google_url,
            google_ads_url=google_ads_url,
            wrike_url=wrike_url,
            slack_url=slack_url,
            google_done=status.google,
            google_ads_done=status.google_ads,
            wrike_done=status.wrike,
            slack_done=status.slack_user_token,
        )

        # Always send the connect card into the bot's DM with the user — all
        # bot conversation lives inside the app, not in channels.
        dm = await client.conversations_open(users=slack_user_id)
        channel = dm["channel"]["id"]
        posted = await client.chat_postMessage(
            channel=channel, text="Connect your accounts", blocks=blocks
        )

        # Remember this card so we can delete it once all three are connected.
        async with session_scope() as session:
            user = await get_or_create_user(
                session, slack_team_id=slack_team_id, slack_user_id=slack_user_id
            )
            user.connect_card_channel_id = channel
            user.connect_card_ts = posted["ts"]
            session.add(user)

        if body.get("channel_id") != channel:
            await client.chat_postEphemeral(
                channel=body["channel_id"],
                user=slack_user_id,
                text="📬 I sent you a DM.",
            )
