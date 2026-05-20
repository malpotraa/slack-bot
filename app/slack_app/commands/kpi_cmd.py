"""/kpi — approved Google Ads KPI pull for one account under the configured MCC."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from app.db.engine import session_scope
from app.db.users import get_or_create_user
from app.integrations import google_ads
from app.slack_app.approval import build_approval_blocks
from app.slack_app.connect_prompt import connect_prompt_blocks
from app.slack_app.connection_status import status_for
from app.utils.slack_mrkdwn import escape_slack_text

KPI_ACCOUNT_SELECT_ACTION = "kpi_google_account_select"
KPI_PROVIDER = "google"
KPI_MODES = {"cpa", "roas"}


def register(app):
    @app.command("/kpi")
    async def handle_kpi(ack, body, client):
        await ack()
        slack_team_id = body["team_id"]
        slack_user_id = body["user_id"]

        parsed = _parse_command(body.get("text") or "")

        # Run like the other commands: all KPI output lands in the bot's DM
        # with the user. The slash command only echoes a pointer in the origin
        # channel when it was invoked outside that DM.
        dm = await client.conversations_open(users=slack_user_id)
        target_channel = dm["channel"]["id"]
        invoked_in_dm = body.get("channel_id") == target_channel

        if parsed.get("error"):
            thread_ts, _status_ts = await _post_kpi_root(
                client, target_channel, "📊 KPI command needs an account name."
            )
            await _post_usage(client, target_channel, thread_ts, parsed["error"])
            await _notify_dm_if_needed(client, body, slack_user_id, invoked_in_dm)
            return

        thread_ts, status_ts = await _post_kpi_root(
            client,
            target_channel,
            (
                "🔎 Looking up Google Ads accounts for "
                f"`{escape_slack_text(parsed['account_query'])}`..."
            ),
        )
        await _notify_dm_if_needed(client, body, slack_user_id, invoked_in_dm)

        user_info = await client.users_info(user=slack_user_id)
        slack_user = user_info.get("user", {})
        profile = slack_user.get("profile") or {}
        async with session_scope() as session:
            user = await get_or_create_user(
                session,
                slack_team_id=slack_team_id,
                slack_user_id=slack_user_id,
                email=profile.get("email"),
                real_name=slack_user.get("real_name") or slack_user.get("name"),
                tz=slack_user.get("tz"),
            )
            user_id = user.id

        status = await status_for(slack_team_id, slack_user_id)
        if not status.google_ads:
            await _update_thread_status(
                client,
                target_channel,
                status_ts,
                "🔐 Google Ads needs to be connected before I can pull KPI data.",
            )
            await _post_google_ads_connect_prompt(
                client, target_channel, thread_ts, slack_team_id, slack_user_id
            )
            return

        try:
            ads_client = await google_ads.GoogleAdsClient.for_user(user_id)  # type: ignore[arg-type]
            matches = await ads_client.search_accounts(parsed["account_query"])
        except google_ads.GoogleAdsNotConnectedError:
            await _update_thread_status(
                client,
                target_channel,
                status_ts,
                "🔐 Google Ads needs to be connected before I can pull KPI data.",
            )
            await _post_google_ads_connect_prompt(
                client, target_channel, thread_ts, slack_team_id, slack_user_id
            )
            return
        except google_ads.GoogleAdsConfigError as exc:
            await _post_thread_message(
                client,
                target_channel,
                thread_ts,
                f"⚠️ Google Ads is not configured yet: {escape_slack_text(exc)}",
            )
            return
        except Exception:
            logger.exception("Google Ads account search failed")
            await _post_thread_message(
                client,
                target_channel,
                thread_ts,
                "⚠️ Google Ads account search failed — please try again in a moment.",
            )
            return

        if not matches:
            await _update_thread_status(
                client,
                target_channel,
                status_ts,
                (
                    "No enabled Google Ads account under the configured MCC matched "
                    f"`{escape_slack_text(parsed['account_query'])}`."
                ),
            )
            return

        await _update_thread_status(
            client,
            target_channel,
            status_ts,
            "✅ Account lookup complete. Review the KPI request below.",
        )
        if len(matches) == 1:
            blocks = _approval_blocks_for_account(matches[0], parsed["mode"], parsed["account_query"])
            await client.chat_postMessage(
                channel=target_channel,
                thread_ts=thread_ts,
                text="Approve Google Ads KPI pull",
                blocks=blocks,
            )
        else:
            await client.chat_postMessage(
                channel=target_channel,
                thread_ts=thread_ts,
                text="Pick a Google Ads account",
                blocks=_account_choice_blocks(matches, parsed["mode"], parsed["account_query"]),
            )

    @app.action(KPI_ACCOUNT_SELECT_ACTION)
    async def handle_account_select(ack, body, client):
        await ack()
        action = body["actions"][0]
        try:
            payload = json.loads(action["selected_option"]["value"])
            account = google_ads.GoogleAdsAccount(
                customer_id=google_ads.normalize_customer_id(payload["customer_id"]),
                name=str(payload["name"]),
                time_zone=str(payload["time_zone"] or "UTC"),
                currency_code=str(payload["currency_code"] or "USD"),
            )
            mode = payload["mode"] if payload.get("mode") in KPI_MODES else "cpa"
            query = str(payload.get("query") or "")
        except Exception as exc:
            logger.warning(f"kpi account select bad payload: {exc}")
            return

        channel_id = body["channel"]["id"]
        message_ts = body["message"]["ts"]
        blocks = _approval_blocks_for_account(account, mode, query)
        await client.chat_update(
            channel=channel_id,
            ts=message_ts,
            text="Approve Google Ads KPI pull",
            blocks=blocks,
        )


def _parse_command(text: str) -> dict[str, Any]:
    tokens = [t for t in text.split() if t]
    if not tokens or tokens[0].casefold() != KPI_PROVIDER:
        return {"error": "Usage: `/kpi google <account name> [cpa|roas]`."}
    rest = tokens[1:]
    if not rest:
        return {"error": "Add an account search term. Example: `/kpi google jump cpa`."}
    modes = [t.casefold() for t in rest if t.casefold() in KPI_MODES]
    mode = modes[-1] if modes else "cpa"
    account_terms = [t for t in rest if t.casefold() not in KPI_MODES]
    account_query = " ".join(account_terms).strip()
    if not account_query:
        return {"error": "Add an account search term. Example: `/kpi google jump roas`."}
    if len(account_query) > 120:
        return {"error": "Keep the account search term under 120 characters."}
    return {"provider": KPI_PROVIDER, "account_query": account_query, "mode": mode}


def _approval_blocks_for_account(
    account: google_ads.GoogleAdsAccount, mode: str, account_query: str
) -> list[dict]:
    windows = google_ads.compute_windows(account.time_zone)
    metric_name = "ROAS" if mode == "roas" else "Cost / conv."
    metric_emoji = "📈" if mode == "roas" else "🎯"
    summary = (
        f"{metric_emoji} *Google Ads KPI report*\n"
        f"*Account*\n"
        f"{escape_slack_text(account.name)}  `({account.customer_id})`\n\n"
        f"*Metric*\n"
        f"{metric_name}\n\n"
        f"*Date ranges*\n"
        f"• WOW: `{_window_label(windows.last_7)}` vs `{_window_label(windows.previous_7)}`"
    )
    if windows.mtd and windows.previous_mtd:
        summary += (
            f"\n• MOM: `{_window_label(windows.mtd)}` vs "
            f"`{_window_label(windows.previous_mtd)}`"
        )
    else:
        summary += "\n• MOM: unavailable until this month has at least one completed day."
    summary += (
        "\n\n*Scope*\n"
        "Account total, active campaigns, and keyword drivers where available.\n"
        "_Active campaign means currently enabled under the configured MCC._"
    )

    return build_approval_blocks(
        [
            {
                "tool": "fetch_google_ads_kpis",
                "args": {
                    "customer_id": account.customer_id,
                    "account_name": account.name,
                    "mode": mode,
                    "account_query": account_query,
                    "windows": windows.as_dict(),
                },
                "summary": summary,
            }
        ],
        primary_button_text="✅ Pull KPI",
        cancel_button_text="❌ Cancel",
    )


def _account_choice_blocks(
    matches: list[google_ads.GoogleAdsAccount], mode: str, account_query: str
) -> list[dict]:
    options = []
    for account in matches[:25]:
        label = account.name[:75] or account.customer_id
        payload = {
            "customer_id": account.customer_id,
            "name": account.name,
            "time_zone": account.time_zone,
            "currency_code": account.currency_code,
            "mode": mode,
            "query": account_query,
        }
        options.append(
            {
                "text": {"type": "plain_text", "text": label},
                "value": json.dumps(payload, separators=(",", ":"))[:1900],
            }
        )

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"I found {len(matches)} enabled Google Ads accounts matching "
                    f"`{escape_slack_text(account_query)}`. Pick one:"
                ),
            },
            "accessory": {
                "type": "static_select",
                "placeholder": {"type": "plain_text", "text": "Choose account"},
                "action_id": KPI_ACCOUNT_SELECT_ACTION,
                "options": options,
            },
        }
    ]


async def _post_google_ads_connect_prompt(
    client,
    channel_id: str,
    thread_ts: str,
    slack_team_id: str,
    slack_user_id: str,
) -> None:
    posted = await client.chat_postMessage(
        channel=channel_id,
        thread_ts=thread_ts,
        text="Google Ads is not connected yet.",
        blocks=connect_prompt_blocks(
            "google_ads",
            slack_team_id=slack_team_id,
            slack_user_id=slack_user_id,
            reason="Google Ads is not connected yet. Connect it, then run `/kpi` again.",
        ),
    )
    async with session_scope() as session:
        user = await get_or_create_user(
            session, slack_team_id=slack_team_id, slack_user_id=slack_user_id
        )
        user.google_ads_connect_channel_id = channel_id
        user.google_ads_connect_ts = posted["ts"]
        session.add(user)


async def _post_usage(client, channel_id: str, thread_ts: str, text: str) -> None:
    await _post_thread_message(client, channel_id, thread_ts, escape_slack_text(text))


async def _post_kpi_root(client, channel_id: str, text: str) -> tuple[str, str]:
    """Post the KPI status message into the DM; its ts roots the run's thread."""
    posted = await client.chat_postMessage(channel=channel_id, text=text)
    return posted["ts"], posted["ts"]


async def _notify_dm_if_needed(
    client, body, slack_user_id: str, invoked_in_dm: bool
) -> None:
    """Echo a pointer in the origin channel when /kpi was not run in the DM."""
    if invoked_in_dm:
        return
    await client.chat_postEphemeral(
        channel=body["channel_id"],
        user=slack_user_id,
        text="📬 I sent you a DM.",
    )


async def _update_thread_status(
    client, channel_id: str, thread_ts: str, text: str
) -> None:
    await client.chat_update(channel=channel_id, ts=thread_ts, text=text)


async def _post_thread_message(
    client, channel_id: str, thread_ts: str, text: str
) -> None:
    await client.chat_postMessage(channel=channel_id, thread_ts=thread_ts, text=text)


def _window_label(window: google_ads.DateWindow) -> str:
    return f"{window.start.isoformat()} → {window.end.isoformat()}"
