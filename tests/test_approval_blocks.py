"""Approval-card block construction (approval)."""

import json

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


def test_default_intro_is_prepended():
    blocks = build_approval_blocks([_action()])
    assert blocks[0]["type"] == "section"
    assert blocks[0]["text"]["text"] == DEFAULT_APPROVAL_INTRO


def test_empty_intro_suppresses_header():
    blocks = build_approval_blocks([_action()], intro_text="")
    assert blocks[0]["text"]["text"] != DEFAULT_APPROVAL_INTRO


def test_custom_intro_used_verbatim():
    blocks = build_approval_blocks([_action()], intro_text="🔔 Custom")
    assert blocks[0]["text"]["text"] == "🔔 Custom"


def test_buttons_carry_expected_action_ids():
    blocks = build_approval_blocks([_action()])
    actions = next(b for b in blocks if b["type"] == "actions")
    action_ids = {e["action_id"] for e in actions["elements"]}
    assert action_ids == {"agent_approve", "agent_disapprove"}


def test_with_alternates_gets_default_intro():
    blocks = build_approval_blocks_with_alternates(primary=_action())
    assert blocks[0]["text"]["text"] == DEFAULT_APPROVAL_INTRO


def test_with_alternates_includes_alt_button_when_alternate_given():
    blocks = build_approval_blocks_with_alternates(
        primary=_action(),
        alternate={"tool": "create_calendar_event", "args": {}, "summary": "alt"},
    )
    actions = next(b for b in blocks if b["type"] == "actions")
    action_ids = {e["action_id"] for e in actions["elements"]}
    assert action_ids == {"agent_approve", "agent_approve_alt", "agent_disapprove"}


def test_encode_payload_roundtrips_tool_and_args():
    payload = _encode_payload("create_calendar_event", {"title": "x"}, "summary")
    decoded = json.loads(payload)
    assert decoded["tool"] == "create_calendar_event"
    assert decoded["args"] == {"title": "x"}
    assert decoded["summary"] == "summary"
