"""Per-user Google Calendar client. Auto-refreshes access tokens via stored refresh token."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from loguru import logger

from app.config import settings
from app.db import tokens as token_repo
from app.db.engine import session_scope
from app.utils.working_hours import TimeSlot


class CalendarNotConnectedError(RuntimeError):
    pass


async def _credentials_for(user_id: int) -> Credentials:
    """Load + refresh credentials, persisting any new access_token."""
    async with session_scope() as session:
        tok = await token_repo.get_google_token(session, user_id)
        if tok is None:
            raise CalendarNotConnectedError("Google not connected. Run /connect.")
        refresh_token, access_token = token_repo.decrypt_google(tok)
        expiry = tok.access_token_expires_at

    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=tok.scopes.split() if tok.scopes else None,
    )
    # google-auth uses naive UTC for `expiry`. Match that convention.
    if expiry is not None:
        # SQLite drops tzinfo on read — treat naive values as already-UTC.
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        creds.expiry = expiry.astimezone(UTC).replace(tzinfo=None)

    if not creds.valid:
        # Refresh on a thread — google-auth is sync.
        await asyncio.to_thread(creds.refresh, GoogleAuthRequest())
        # Persist the new access token + new expiry.
        new_expiry_utc = (
            creds.expiry.replace(tzinfo=UTC) if creds.expiry else None
        )
        async with session_scope() as session:
            await token_repo.upsert_google_token(
                session,
                user_id=user_id,
                refresh_token=None,  # don't overwrite
                access_token=creds.token,
                access_token_expires_at=new_expiry_utc,
                scopes=" ".join(creds.scopes or []),
                google_email=None,
            )
    return creds


def _build_service(creds: Credentials):
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


async def list_events(
    user_id: int,
    *,
    time_min: datetime,
    time_max: datetime,
    calendar_id: str = "primary",
) -> list[dict[str, Any]]:
    """Return events in [time_min, time_max], single-events expanded, sorted by startTime."""
    creds = await _credentials_for(user_id)

    def _call() -> list[dict[str, Any]]:
        service = _build_service(creds)
        events: list[dict[str, Any]] = []
        page_token = None
        while True:
            resp = (
                service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=time_min.astimezone(UTC).isoformat(),
                    timeMax=time_max.astimezone(UTC).isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    showDeleted=False,
                    pageToken=page_token,
                    maxResults=2500,
                )
                .execute()
            )
            events.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return events

    return await asyncio.to_thread(_call)


async def create_event(
    user_id: int,
    *,
    summary: str,
    description: str,
    start: datetime,
    end: datetime,
    tz_name: str,
    color_id: str | None = "5",
    extended_properties: dict[str, str] | None = None,
    source_title: str | None = None,
    source_url: str | None = None,
    calendar_id: str = "primary",
) -> dict[str, Any]:
    creds = await _credentials_for(user_id)

    body: dict[str, Any] = {
        "summary": summary[:200],
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": tz_name},
        "end": {"dateTime": end.isoformat(), "timeZone": tz_name},
        "reminders": {"useDefault": True},
    }
    if color_id:
        body["colorId"] = color_id
    if extended_properties:
        body["extendedProperties"] = {"private": extended_properties}
    if source_title and source_url:
        body["source"] = {"title": source_title[:200], "url": source_url}

    def _call() -> dict[str, Any]:
        service = _build_service(creds)
        return (
            service.events()
            .insert(calendarId=calendar_id, body=body, sendUpdates="none")
            .execute()
        )

    event = await asyncio.to_thread(_call)
    logger.info(f"Created calendar event {event.get('id')} for user {user_id}: {summary!r}")
    return event


async def get_event(
    user_id: int, *, event_id: str, calendar_id: str = "primary"
) -> dict[str, Any]:
    creds = await _credentials_for(user_id)

    def _call() -> dict[str, Any]:
        service = _build_service(creds)
        return service.events().get(calendarId=calendar_id, eventId=event_id).execute()

    return await asyncio.to_thread(_call)


async def update_event(
    user_id: int,
    *,
    event_id: str,
    summary: str | None = None,
    description: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    tz_name: str | None = None,
    calendar_id: str = "primary",
) -> dict[str, Any]:
    """PATCH an existing event. Only the supplied fields are updated."""
    creds = await _credentials_for(user_id)

    body: dict[str, Any] = {}
    if summary is not None:
        body["summary"] = summary[:200]
    if description is not None:
        body["description"] = description
    if start is not None:
        body["start"] = {"dateTime": start.isoformat()}
        if tz_name:
            body["start"]["timeZone"] = tz_name
    if end is not None:
        body["end"] = {"dateTime": end.isoformat()}
        if tz_name:
            body["end"]["timeZone"] = tz_name

    def _call() -> dict[str, Any]:
        service = _build_service(creds)
        return (
            service.events()
            .patch(
                calendarId=calendar_id,
                eventId=event_id,
                body=body,
                sendUpdates="none",
            )
            .execute()
        )

    event = await asyncio.to_thread(_call)
    logger.info(f"Updated calendar event {event_id} for user {user_id}: keys={list(body)}")
    return event


# ── Helpers for /goodmorning + /wrike ──────────────────────────────────────


def event_is_all_day(event: dict) -> bool:
    return "date" in event.get("start", {}) and "dateTime" not in event.get("start", {})


def event_response_status(event: dict, viewer_email: str | None) -> str:
    """Return user's response status for this event ('accepted', 'declined', etc.)."""
    if not viewer_email:
        return "accepted"
    for a in event.get("attendees", []) or []:
        if a.get("self") or (a.get("email", "").lower() == viewer_email.lower()):
            return a.get("responseStatus", "accepted")
    return "accepted"


def event_to_busy_slot(event: dict) -> TimeSlot | None:
    """Convert a Calendar event to a TimeSlot. Returns None for all-day or zero-length."""
    if event_is_all_day(event):
        return None
    start = event["start"].get("dateTime")
    end = event["end"].get("dateTime")
    if not start or not end:
        return None
    s = datetime.fromisoformat(start.replace("Z", "+00:00"))
    e = datetime.fromisoformat(end.replace("Z", "+00:00"))
    if e <= s:
        return None
    if event.get("transparency") == "transparent":
        # User marked themselves as Free for this event.
        return None
    return TimeSlot(start=s, end=e)


async def find_overlaps(
    user_id: int,
    start: datetime,
    end: datetime,
    *,
    exclude_event_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return a compact summary of events that overlap [start, end].

    Skips: declined events, transparent (Free) events, all-day events, and the
    event whose id matches `exclude_event_id` (used when updating an event so
    we don't conflict-detect against itself).
    """
    from datetime import timedelta as _td

    events = await list_events(
        user_id, time_min=start - _td(hours=4), time_max=end + _td(hours=4)
    )
    overlaps: list[dict[str, Any]] = []
    for ev in events:
        if exclude_event_id and ev.get("id") == exclude_event_id:
            continue
        if event_response_status(ev, None) == "declined":
            continue
        slot = event_to_busy_slot(ev)
        if slot is None:
            continue
        if slot.start < end and start < slot.end:
            overlaps.append(
                {
                    "id": ev.get("id"),
                    "title": ev.get("summary"),
                    "start": ev["start"]["dateTime"],
                    "end": ev["end"]["dateTime"],
                }
            )
    return overlaps


async def busy_slots_in_horizon(
    user_id: int, anchor: datetime, *, days: int = 5
) -> list[TimeSlot]:
    """Pull busy TimeSlots for [anchor-4h, anchor + days]. Used by the
    free-slot finder when suggesting an alternative time."""
    from datetime import timedelta as _td

    events = await list_events(
        user_id, time_min=anchor - _td(hours=4), time_max=anchor + _td(days=days)
    )
    return [s for s in (event_to_busy_slot(ev) for ev in events) if s]


def classify_event(event: dict) -> str:
    """Rough categorization for the /goodmorning summary."""
    summary = (event.get("summary") or "").lower()
    if any(k in summary for k in ("focus", "deep work", "blocked", "do not schedule", "dnd")):
        return "blocked"
    if (event.get("attendees") or []) and len(event["attendees"]) > 1:
        return "meeting"
    return "blocked"  # zero/one-attendee events default to "personal block"
