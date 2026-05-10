"""Gap-finder used by /goodmorning and /wrike to compute free time slots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from app.utils.timezone import user_tz


@dataclass(frozen=True)
class TimeSlot:
    start: datetime  # tz-aware in user_tz
    end: datetime

    @property
    def duration_min(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    def overlaps(self, other: TimeSlot) -> bool:
        return self.start < other.end and other.start < self.end


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def working_window(
    on_date: datetime, workday_start: str, workday_end: str, tz_name: str | None
) -> TimeSlot:
    """Working window for a given date in the user's timezone."""
    z = user_tz(tz_name)
    d = on_date.astimezone(z).date()
    return TimeSlot(
        start=datetime.combine(d, parse_hhmm(workday_start), tzinfo=z),
        end=datetime.combine(d, parse_hhmm(workday_end), tzinfo=z),
    )


def merge_busy(busy: list[TimeSlot]) -> list[TimeSlot]:
    """Merge overlapping busy intervals."""
    if not busy:
        return []
    sorted_busy = sorted(busy, key=lambda s: s.start)
    merged: list[TimeSlot] = [sorted_busy[0]]
    for slot in sorted_busy[1:]:
        last = merged[-1]
        if slot.start <= last.end:
            merged[-1] = TimeSlot(start=last.start, end=max(last.end, slot.end))
        else:
            merged.append(slot)
    return merged


def find_free_slots(
    work_window: TimeSlot,
    busy: list[TimeSlot],
    *,
    min_minutes: int = 30,
) -> list[TimeSlot]:
    """Free slots ≥ min_minutes inside the working window, after subtracting busy."""
    busy_in_window = [
        TimeSlot(max(b.start, work_window.start), min(b.end, work_window.end))
        for b in busy
        if b.end > work_window.start and b.start < work_window.end
    ]
    busy_in_window = [s for s in busy_in_window if s.end > s.start]
    busy_in_window = merge_busy(busy_in_window)

    free: list[TimeSlot] = []
    cursor = work_window.start
    for b in busy_in_window:
        if b.start - cursor >= timedelta(minutes=min_minutes):
            free.append(TimeSlot(cursor, b.start))
        cursor = max(cursor, b.end)
    if work_window.end - cursor >= timedelta(minutes=min_minutes):
        free.append(TimeSlot(cursor, work_window.end))
    return free


def free_busy_summary(
    work_window: TimeSlot, busy: list[TimeSlot]
) -> tuple[timedelta, timedelta, list[TimeSlot]]:
    """Return (busy_total, free_total, free_slots≥30m) inside the working window."""
    free_slots = find_free_slots(work_window, busy, min_minutes=30)
    free_total = sum((s.end - s.start for s in free_slots), timedelta())
    busy_total = (work_window.end - work_window.start) - free_total
    if busy_total < timedelta(0):
        busy_total = timedelta(0)
    return busy_total, free_total, free_slots


def first_free_slot_for_duration(
    busy: list[TimeSlot],
    *,
    duration_minutes: int,
    horizon_days: int,
    workday_start: str,
    workday_end: str,
    tz_name: str | None,
    not_before: datetime | None = None,
) -> TimeSlot | None:
    """Walk forward day-by-day inside working hours, return the first slot of given length.

    `not_before` lets the caller require the slot to start at/after a given time
    (e.g. "no earlier than the user's proposed start").
    """
    z = user_tz(tz_name)
    today = (not_before or datetime.now(tz=z)).astimezone(z)

    for offset in range(horizon_days):
        day = today + timedelta(days=offset)
        ww = working_window(day, workday_start, workday_end, tz_name)
        # On day 0, clip the window to start at not_before if it's later.
        if offset == 0 and not_before and not_before > ww.start:
            ww = TimeSlot(start=not_before, end=ww.end)
        if ww.end <= ww.start:
            continue
        slots = find_free_slots(ww, busy, min_minutes=duration_minutes)
        if slots:
            s = slots[0]
            return TimeSlot(start=s.start, end=s.start + timedelta(minutes=duration_minutes))
    return None
