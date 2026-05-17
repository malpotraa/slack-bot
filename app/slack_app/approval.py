"""Approve / Disapprove Block Kit card + Bolt action handlers.

When the agent calls a write tool with `confirmed=false`, the runner captures
a pending_action. The handler posts an approval card (this module's
`build_approval_blocks`) with two buttons. Clicking a button fires either
`@app.action("agent_approve")` or `@app.action("agent_disapprove")` (this
module). Approve re-dispatches the tool with confirmed=true. Disapprove
just marks the card cancelled.

Button `value` carries a JSON payload: {"tool": ..., "args": ...}.
Slack's per-value limit is 2000 chars; our tool args are well under that.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger
from slack_sdk.errors import SlackApiError

from app.agent.tools import dispatch_tool
from app.config import settings
from app.db.engine import session_scope
from app.db.users import get_user_by_slack_id
from app.sessions import store as session_store


APPROVE_ACTION_ID = "agent_approve"
APPROVE_ALT_ACTION_ID = "agent_approve_alt"
DISAPPROVE_ACTION_ID = "agent_disapprove"

# Defense-in-depth: only these tool names may be dispatched via the approval-
# button handler. Slack signs the outer interaction (Bolt verifies), but the
# payload JSON we put in the button `value` is not signed by us. Restricting
# to a known write-tool list means even a forged or replayed payload can only
# trigger one of our intended actions — not, say, list-only tools or any
# future tool a future change adds inadvertently.
WRITE_TOOL_ALLOWLIST: set[str] = {
    "create_calendar_event",
    "update_calendar_event",
    "update_wrike_task_status",
    "post_wrike_task_comment",
    "schedule_wrike_task",
    "update_working_hours",
    "update_user_notes",
}


def build_approval_blocks(
    pending_actions: list[dict[str, Any]],
    *,
    intro_text: str | None = None,
    primary_button_text: str = "✅ Confirm",
    cancel_button_text: str = "❌ Cancel",
) -> list[dict[str, Any]]:
    """Build Block Kit blocks for the approval card (no alternate / no conflict).

    intro_text: optional framing line; omit for a tighter card.
    pending_actions: list of {tool, args, summary} dicts from the runner.
    """
    blocks: list[dict[str, Any]] = []
    if intro_text:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": intro_text}}
        )

    for i, action in enumerate(pending_actions):
        summary = action.get("summary") or f"Call `{action['tool']}`"
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": summary}}
        )
        payload = _encode_payload(action["tool"], action.get("args") or {}, summary)
        blocks.append(
            {
                "type": "actions",
                "block_id": f"approval_{i}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": primary_button_text},
                        "style": "primary",
                        "action_id": APPROVE_ACTION_ID,
                        "value": payload,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": cancel_button_text},
                        "style": "danger",
                        "action_id": DISAPPROVE_ACTION_ID,
                        "value": payload,
                    },
                ],
            }
        )

    return blocks


def _encode_payload(tool: str, args: dict[str, Any], summary: str) -> str:
    payload = json.dumps({"tool": tool, "args": args, "summary": summary[:300]})
    if len(payload) > 1900:
        payload = json.dumps(
            {"tool": tool, "args": args, "summary": "(summary truncated)", "_truncated": True}
        )[:1990]
    return payload


def build_approval_blocks_with_alternates(
    *,
    primary: dict[str, Any],
    alternate: dict[str, Any] | None = None,
    intro_text: str | None = None,
    primary_button_text: str = "✅ Confirm",
    alternate_button_text: str = "🔁 Use alternate",
    cancel_button_text: str = "❌ Cancel",
) -> list[dict[str, Any]]:
    """Build an approval card with up to two paths plus Cancel.

    primary: {tool, args, summary}
    alternate: optional second {tool, args, summary, short_time}
        (e.g. the suggested free slot when the proposed slot overlaps)
    intro_text: optional framing line. The conversational-agent card omits
        this since the primary summary already carries the title + context.
        The /wrike command supplies an "Overlap detected" intro.
    """
    blocks: list[dict[str, Any]] = []
    if intro_text:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": intro_text}}
        )
    body = primary.get("summary") or primary["tool"]
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": body}})
    if alternate and alternate.get("summary"):
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"💡 {alternate['summary']}"},
            }
        )

    elements: list[dict[str, Any]] = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": primary_button_text},
            "style": "primary",
            "action_id": APPROVE_ACTION_ID,
            "value": _encode_payload(
                primary["tool"], primary["args"], primary.get("summary") or ""
            ),
        }
    ]
    if alternate:
        elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": alternate_button_text},
                "action_id": APPROVE_ALT_ACTION_ID,
                "value": _encode_payload(
                    alternate["tool"],
                    alternate["args"],
                    alternate.get("summary") or "",
                ),
            }
        )
    elements.append(
        {
            "type": "button",
            "text": {"type": "plain_text", "text": cancel_button_text},
            "style": "danger",
            "action_id": DISAPPROVE_ACTION_ID,
            "value": _encode_payload("(none)", {}, "cancelled"),
        }
    )
    blocks.append({"type": "actions", "block_id": "approval_multi", "elements": elements})
    return blocks


async def _resolve_user_ctx(slack_team_id: str, slack_user_id: str):
    """Look up the User row + return the context the dispatcher needs."""
    async with session_scope() as session:
        user = await get_user_by_slack_id(session, slack_team_id, slack_user_id)
        if user is None or user.id is None:
            return None
        return {
            "user_id": user.id,
            "user_tz": user.tz or "UTC",
            "workday_start": user.workday_start,
            "workday_end": user.workday_end,
        }


def _settled_blocks(emoji: str, msg: str) -> list[dict[str, Any]]:
    """The card after Approve / Disapprove is clicked — no more buttons."""
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{emoji} {msg}"},
        }
    ]


async def _handle_approve_click(body, client) -> None:
    try:
        payload = json.loads(body["actions"][0]["value"])
    except Exception as exc:
        logger.warning(f"approve: bad payload: {exc}")
        return

    tool_name = payload.get("tool")
    args = dict(payload.get("args") or {})
    args["confirmed"] = True

    slack_user_id = body["user"]["id"]
    slack_team_id = (
        body.get("team", {}).get("id") or body.get("user", {}).get("team_id") or ""
    )
    channel_id = body["channel"]["id"]
    message_ts = body["message"]["ts"]

    # Allow-list guard: refuse to dispatch anything not on the known write-tool
    # list, no matter what the payload says. Logs the attempt for auditability.
    if tool_name not in WRITE_TOOL_ALLOWLIST:
        logger.warning(
            f"approval rejected: tool {tool_name!r} not in WRITE_TOOL_ALLOWLIST; "
            f"clicker={slack_user_id} channel={channel_id} ts={message_ts}"
        )
        try:
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text="⚠️ Rejected — that action isn't permitted.",
                blocks=_settled_blocks(
                    "⚠️", "*Rejected.* That action isn't on the allow-list."
                ),
            )
        except SlackApiError as exc:
            logger.warning(f"approve: rejection chat_update failed: {exc}")
        return

    ctx = await _resolve_user_ctx(slack_team_id, slack_user_id)
    if ctx is None:
        await client.chat_update(
            channel=channel_id,
            ts=message_ts,
            text="⚠️ Could not look up your account; please re-run the request.",
            blocks=_settled_blocks(
                "⚠️", "*Failed.* Could not look up your account; please re-run the request."
            ),
        )
        return

    try:
        result = await dispatch_tool(
            tool_name,
            args,
            user_id=ctx["user_id"],
            user_tz=ctx["user_tz"],
            workday_start=ctx["workday_start"],
            workday_end=ctx["workday_end"],
            slack_user_id=slack_user_id,
            slack_bot_token=settings.slack_bot_token,
        )
    except Exception as exc:
        logger.exception(f"approve: dispatch_tool({tool_name}) failed")
        result = {"error": str(exc)}

    if isinstance(result, dict) and result.get("ok"):
        msg = result.get("message") or "Done."
        text = f"✅ {msg}"
        blocks = _settled_blocks("✅", f"*Approved.* {msg}")
        outcome = "approved"
        summary = msg
    else:
        err = (
            result.get("error", "Unknown error")
            if isinstance(result, dict)
            else "Unknown error"
        )
        text = f"⚠️ Failed: {err}"
        blocks = _settled_blocks("⚠️", f"*Failed.* {err}")
        outcome = "failed"
        summary = err

    try:
        await client.chat_update(channel=channel_id, ts=message_ts, text=text, blocks=blocks)
    except SlackApiError as exc:
        logger.warning(f"approve: chat_update failed: {exc}")

    # Best-effort: record the settled outcome into thread history so the next
    # agent turn ("do that again for next week") has context.
    thread_ts = body.get("message", {}).get("thread_ts") or message_ts
    try:
        await session_store.append_settled_action(
            user_id=ctx["user_id"],
            channel_id=channel_id,
            thread_ts=thread_ts,
            outcome=outcome,
            summary=summary,
        )
    except Exception as exc:
        logger.warning(f"approve: history append failed: {exc}")


def register(app) -> None:
    @app.action(APPROVE_ACTION_ID)
    async def handle_approve(ack, body, client):
        await ack()
        await _handle_approve_click(body, client)

    @app.action(APPROVE_ALT_ACTION_ID)
    async def handle_approve_alt(ack, body, client):
        await ack()
        await _handle_approve_click(body, client)

    @app.action(DISAPPROVE_ACTION_ID)
    async def handle_disapprove(ack, body, client):
        await ack()
        channel_id = body["channel"]["id"]
        message_ts = body["message"]["ts"]
        try:
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                text="❌ Cancelled.",
                blocks=_settled_blocks("❌", "*Cancelled.* No changes made."),
            )
        except SlackApiError as exc:
            logger.warning(f"disapprove: chat_update failed: {exc}")
        # Record cancellation into thread history.
        slack_user_id = body["user"]["id"]
        slack_team_id = (
            body.get("team", {}).get("id")
            or body.get("user", {}).get("team_id")
            or ""
        )
        thread_ts = body.get("message", {}).get("thread_ts") or message_ts
        try:
            ctx = await _resolve_user_ctx(slack_team_id, slack_user_id)
            if ctx is not None:
                await session_store.append_settled_action(
                    user_id=ctx["user_id"],
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    outcome="cancelled",
                    summary="user cancelled the proposed action",
                )
        except Exception as exc:
            logger.warning(f"disapprove: history append failed: {exc}")
