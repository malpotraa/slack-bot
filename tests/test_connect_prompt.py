"""Shared connect-prompt card builder (connect_prompt)."""

import pytest

from app.slack_app import connect_prompt


def test_provider_label():
    assert connect_prompt.provider_label("google_ads") == "Google Ads"
    assert connect_prompt.provider_label("wrike") == "Wrike"


def test_connect_button_shape():
    btn = connect_prompt.connect_button("wrike", slack_team_id="T1", slack_user_id="U1")
    assert btn["type"] == "button"
    assert btn["text"]["text"] == "Connect Wrike"
    assert btn["action_id"] == "connect_wrike"
    assert btn["url"].startswith("http")


def test_connect_prompt_blocks_shape():
    blocks = connect_prompt.connect_prompt_blocks(
        "google_ads",
        slack_team_id="T1",
        slack_user_id="U1",
        reason="Connect it.",
    )
    assert len(blocks) == 1
    section = blocks[0]
    assert section["type"] == "section"
    assert section["text"]["text"] == "Connect it."
    assert section["accessory"]["action_id"] == "connect_google_ads"


def test_all_providers_build_a_button():
    # action_ids must match the no-op handlers registered in commands/connect.py.
    for provider in ("google", "google_ads", "wrike", "slack_user"):
        btn = connect_prompt.connect_button(
            provider, slack_team_id="T1", slack_user_id="U1"
        )
        assert btn["action_id"].startswith("connect_")
        assert btn["url"].startswith("http")


def test_unknown_provider_raises():
    with pytest.raises(KeyError):
        connect_prompt.connect_button("nope", slack_team_id="T", slack_user_id="U")
