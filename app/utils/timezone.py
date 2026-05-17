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


def fmt_local_dt(dt: datetime, tz_name: str | None) -> str:
    """Render a datetime in the user's tz as 'Mon May 18, 10:00am'."""
    local = dt.astimezone(user_tz(tz_name))
    return local.strftime("%a %b %-d, %-I:%M%p").replace("AM", "am").replace("PM", "pm")


def fmt_local_range(start: datetime, end: datetime, tz_name: str | None) -> str:
    """Render a range in the user's tz as 'Mon May 18, 10:00am – 10:30am'.

    If start and end fall on different local dates, the end side gets the
    full 'Tue May 19, 10:30am' instead of just the time.
    """
    z = user_tz(tz_name)
    s, e = start.astimezone(z), end.astimezone(z)
    s_str = s.strftime("%a %b %-d, %-I:%M%p").replace("AM", "am").replace("PM", "pm")
    if s.date() == e.date():
        e_str = e.strftime("%-I:%M%p").replace("AM", "am").replace("PM", "pm")
    else:
        e_str = e.strftime("%a %b %-d, %-I:%M%p").replace("AM", "am").replace("PM", "pm")
    return f"{s_str} – {e_str}"
