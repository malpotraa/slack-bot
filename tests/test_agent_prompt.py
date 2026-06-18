"""Prompt contract checks for high-risk Ads behavior."""

from app.agent.prompts import ASSISTANT_SYSTEM_PROMPT


def test_ads_prompt_forbids_unverified_self_diagnosis():
    assert "Do NOT self-diagnose a prior answer as fabricated" in ASSISTANT_SYSTEM_PROMPT
    assert "where did that percentage come" in ASSISTANT_SYSTEM_PROMPT
