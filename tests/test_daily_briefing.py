"""Opt-in daily /goodmorning briefing: time parsing, controls block, scheduler."""

from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.slack_app.commands.goodmorning import (
    GM_SUBSCRIBE_ACTION,
    GM_UNSUBSCRIBE_ACTION,
    _controls_block,
    _parse_hhmm,
)
from app.slack_app import scheduler


# ── time parsing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("08:00", "08:00"),
        ("7:30", "07:30"),
        ("23:59", "23:59"),
        ("00:00", "00:00"),
        (" 9:05 ", "09:05"),
    ],
)
def test_parse_hhmm_accepts_valid(raw, expected):
    assert _parse_hhmm(raw) == expected


@pytest.mark.parametrize("raw", ["24:00", "08:60", "8", "8am", "", None, "99:99"])
def test_parse_hhmm_rejects_invalid(raw):
    assert _parse_hhmm(raw) is None


# ── controls block ───────────────────────────────────────────────────────────


def test_controls_block_toggles_action_and_is_swappable():
    off = _controls_block(enabled=False, time_str="08:00")
    on = _controls_block(enabled=True, time_str="08:00")
    # stable block_id so the action handler can swap it in place
    assert off["block_id"] == on["block_id"] == "gm_daily_controls"
    assert off["elements"][0]["action_id"] == GM_SUBSCRIBE_ACTION
    assert on["elements"][0]["action_id"] == GM_UNSUBSCRIBE_ACTION


# ── scheduler ────────────────────────────────────────────────────────────────


def _subscriber(uid: int, *, tz: str = "UTC", time: str = "08:00"):
    return SimpleNamespace(
        id=uid,
        slack_team_id="T1",
        slack_user_id=f"U{uid}",
        real_name="Tester",
        tz=tz,
        workday_start="09:00",
        workday_end="18:00",
        email=None,
        daily_briefing_time=time,
    )


@pytest.fixture
def patched_scheduler(monkeypatch):
    """Stub out the DB + clock so the scheduler logic runs without a database."""
    state = {"subscribers": [], "now": datetime(2026, 6, 19, 8, 0), "sent": []}

    @asynccontextmanager
    async def fake_session_scope():
        yield None

    async def fake_list(_session):
        return state["subscribers"]

    async def fake_post_briefing(client, **kwargs):
        state["sent"].append(kwargs["user_id"])

    monkeypatch.setattr(scheduler, "session_scope", fake_session_scope)
    monkeypatch.setattr(scheduler, "list_daily_briefing_subscribers", fake_list)
    monkeypatch.setattr(scheduler, "post_briefing", fake_post_briefing)
    monkeypatch.setattr(scheduler, "now_in", lambda tz: state["now"])
    scheduler._last_sent.clear()
    return state


async def test_scheduler_sends_only_at_matching_local_time(patched_scheduler):
    patched_scheduler["subscribers"] = [
        _subscriber(1, time="08:00"),  # matches
        _subscriber(2, time="09:00"),  # not yet
    ]
    await scheduler._run_due_briefings(client=object())
    assert patched_scheduler["sent"] == [1]


async def test_scheduler_sends_at_most_once_per_day(patched_scheduler):
    patched_scheduler["subscribers"] = [_subscriber(1, time="08:00")]
    await scheduler._run_due_briefings(client=object())
    await scheduler._run_due_briefings(client=object())  # same minute/day → no resend
    assert patched_scheduler["sent"] == [1]


async def test_scheduler_respects_per_user_timezone(patched_scheduler):
    # Same wall-clock 08:00 UTC, but each user's chosen time is local to their tz.
    patched_scheduler["now"] = datetime(2026, 6, 19, 8, 0)
    patched_scheduler["subscribers"] = [_subscriber(1, tz="UTC", time="08:00")]
    await scheduler._run_due_briefings(client=object())
    assert patched_scheduler["sent"] == [1]


async def test_scheduler_isolates_per_user_failures(patched_scheduler, monkeypatch):
    async def boom(client, **kwargs):
        if kwargs["user_id"] == 1:
            raise RuntimeError("slack down")
        patched_scheduler["sent"].append(kwargs["user_id"])

    monkeypatch.setattr(scheduler, "post_briefing", boom)
    patched_scheduler["subscribers"] = [
        _subscriber(1, time="08:00"),
        _subscriber(2, time="08:00"),
    ]
    await scheduler._run_due_briefings(client=object())  # must not raise
    assert patched_scheduler["sent"] == [2]
