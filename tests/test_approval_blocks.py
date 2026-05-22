"""Approval-card block construction (approval)."""

import json

import pytest

from app.slack_app import approval
from app.slack_app.approval import (
    DEFAULT_APPROVAL_INTRO,
    _encode_payload,
    build_approval_blocks,
    build_approval_blocks_with_alternates,
)


def _action():
    return {
        "tool": "create_calendar_event",
        "args": {"title": "x"},
        "summary": "Create *x*",
    }


@pytest.fixture(autouse=True)
def fake_approval_store(monkeypatch):
    async def _fake_store(*, user_id, action_id, tool, args, summary):
        return _encode_payload(f"tok-{action_id}")

    monkeypatch.setattr(approval, "_store_payload", _fake_store)


@pytest.mark.asyncio
async def test_default_intro_is_prepended():
    blocks = await build_approval_blocks([_action()], user_id=123)
    assert blocks[0]["type"] == "section"
    assert blocks[0]["text"]["text"] == DEFAULT_APPROVAL_INTRO


@pytest.mark.asyncio
async def test_empty_intro_suppresses_header():
    blocks = await build_approval_blocks([_action()], user_id=123, intro_text="")
    assert blocks[0]["text"]["text"] != DEFAULT_APPROVAL_INTRO


@pytest.mark.asyncio
async def test_custom_intro_used_verbatim():
    blocks = await build_approval_blocks([_action()], user_id=123, intro_text="🔔 Custom")
    assert blocks[0]["text"]["text"] == "🔔 Custom"


@pytest.mark.asyncio
async def test_buttons_carry_expected_action_ids():
    blocks = await build_approval_blocks([_action()], user_id=123)
    actions = next(b for b in blocks if b["type"] == "actions")
    action_ids = {e["action_id"] for e in actions["elements"]}
    assert action_ids == {"agent_approve", "agent_disapprove"}


@pytest.mark.asyncio
async def test_with_alternates_gets_default_intro():
    blocks = await build_approval_blocks_with_alternates(user_id=123, primary=_action())
    assert blocks[0]["text"]["text"] == DEFAULT_APPROVAL_INTRO


@pytest.mark.asyncio
async def test_with_alternates_includes_alt_button_when_alternate_given():
    blocks = await build_approval_blocks_with_alternates(
        user_id=123,
        primary=_action(),
        alternate={"tool": "create_calendar_event", "args": {}, "summary": "alt"},
    )
    actions = next(b for b in blocks if b["type"] == "actions")
    action_ids = {e["action_id"] for e in actions["elements"]}
    assert action_ids == {"agent_approve", "agent_approve_alt", "agent_disapprove"}


def test_encode_payload_contains_only_opaque_token():
    payload = _encode_payload("opaque-token")
    decoded = json.loads(payload)
    assert decoded == {"approval_token": "opaque-token"}
    assert "args" not in decoded
    assert "tool" not in decoded
