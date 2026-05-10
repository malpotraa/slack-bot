"""Timezone helpers."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


def user_tz(tz_name: str | None) -> ZoneInfo:
    """Return ZoneInfo for user, or UTC if unset/unknown."""
    if not tz_name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def now_in(tz_name: str | None) -> datetime:
    return datetime.now(tz=user_tz(tz_name))


def start_of_day(d: datetime, tz_name: str | None) -> datetime:
    z = user_tz(tz_name)
    return datetime.combine(d.astimezone(z).date(), time.min, tzinfo=z)


def end_of_day(d: datetime, tz_name: str | None) -> datetime:
    z = user_tz(tz_name)
    return datetime.combine(d.astimezone(z).date(), time.max, tzinfo=z)


def to_utc(d: datetime) -> datetime:
    return d.astimezone(UTC)


def days_ago(n: int, tz_name: str | None) -> datetime:
    return now_in(tz_name) - timedelta(days=n)
