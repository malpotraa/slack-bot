"""Per-user Wrike REST client. Auto-refreshes access tokens."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from loguru import logger
from sqlmodel import select

from app.db import tokens as token_repo
from app.db.engine import session_scope
from app.db.models import WorkflowStatusCache
from app.oauth import wrike as wrike_oauth


class WrikeNotConnectedError(RuntimeError):
    pass


@dataclass
class WrikeAuth:
    access_token: str
    api_host: str  # e.g. "www.wrike.com" or "app-us2.wrike.com"

    @property
    def base_url(self) -> str:
        return f"https://{self.api_host}/api/v4"


async def _auth_for(user_id: int) -> WrikeAuth:
    async with session_scope() as session:
        tok = await token_repo.get_wrike_token(session, user_id)
        if tok is None:
            raise WrikeNotConnectedError("Wrike not connected. Run /connect.")
        refresh_token, access_token = token_repo.decrypt_wrike(tok)
        expires_at = tok.access_token_expires_at
        api_host = tok.api_host

    # SQLite (and old Postgres rows) return naive datetimes. Treat them as UTC.
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if expires_at <= datetime.now(UTC) + timedelta(seconds=30):
        logger.info(f"Refreshing Wrike token for user {user_id}")
        body = await wrike_oauth.refresh_access_token(refresh_token)
        access_token = body["access_token"]
        new_refresh = body.get("refresh_token", refresh_token)
        new_host = body.get("host", api_host)
        new_expires = wrike_oauth.expires_at_from_seconds(body.get("expires_in"))
        async with session_scope() as session:
            await token_repo.upsert_wrike_token(
                session,
                user_id=user_id,
                refresh_token=new_refresh,
                access_token=access_token,
                access_token_expires_at=new_expires,
                api_host=new_host,
            )
        api_host = new_host

    return WrikeAuth(access_token=access_token, api_host=api_host)


class WrikeClient:
    def __init__(self, auth: WrikeAuth):
        self._auth = auth

    @classmethod
    async def for_user(cls, user_id: int) -> WrikeClient:
        return cls(await _auth_for(user_id))

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._auth.access_token}"}

    async def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self._auth.base_url}{path}"
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.get(url, headers=self._headers(), params=params)
            if resp.status_code != 200:
                logger.error(f"Wrike GET {path} {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            return resp.json()

    async def _put(self, path: str, params: dict | None = None) -> dict:
        url = f"{self._auth.base_url}{path}"
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.put(url, headers=self._headers(), params=params)
            if resp.status_code not in (200, 201):
                logger.error(f"Wrike PUT {path} {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            return resp.json()

    async def _post(self, path: str, params: dict | None = None) -> dict:
        url = f"{self._auth.base_url}{path}"
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.post(url, headers=self._headers(), params=params)
            if resp.status_code not in (200, 201):
                logger.error(f"Wrike POST {path} {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            return resp.json()

    # ── User identity ──────────────────────────────────────────────────
    async def me(self) -> dict:
        data = await self._get("/contacts", params={"me": "true"})
        contacts = data.get("data", [])
        return contacts[0] if contacts else {}

    # ── Workflows / custom statuses ────────────────────────────────────
    async def workflows(self) -> list[dict]:
        return (await self._get("/workflows")).get("data", [])

    # ── Single-task ────────────────────────────────────────────────────
    async def task(self, task_id: str, *, fields: list[str] | None = None) -> dict | None:
        params: dict[str, Any] = {}
        if fields:
            params["fields"] = json.dumps(fields)
        data = (await self._get(f"/tasks/{task_id}", params=params)).get("data") or []
        return data[0] if data else None

    async def update_task_status(self, task_id: str, *, custom_status_id: str) -> dict | None:
        params = {"customStatus": custom_status_id}
        data = (await self._put(f"/tasks/{task_id}", params=params)).get("data") or []
        return data[0] if data else None

    async def post_task_comment(self, task_id: str, *, text: str) -> dict | None:
        params = {"text": text}
        data = (await self._post(f"/tasks/{task_id}/comments", params=params)).get("data") or []
        return data[0] if data else None

    # ── Tasks ──────────────────────────────────────────────────────────
    async def tasks(
        self,
        *,
        responsibles: list[str] | None = None,
        custom_statuses: list[str] | None = None,
        status: str | None = None,
        due_date_start: datetime | None = None,
        due_date_end: datetime | None = None,
        fields: list[str] | None = None,
    ) -> list[dict]:
        params: dict[str, Any] = {}
        if responsibles:
            params["responsibles"] = json.dumps(responsibles)
        if custom_statuses:
            params["customStatuses"] = json.dumps(custom_statuses)
        if status:
            params["status"] = status
        if due_date_start or due_date_end:
            payload: dict[str, str] = {}
            if due_date_start:
                payload["start"] = due_date_start.date().isoformat()
            if due_date_end:
                payload["end"] = due_date_end.date().isoformat()
            params["dueDate"] = json.dumps(payload)
        if fields:
            params["fields"] = json.dumps(fields)
        return (await self._get("/tasks", params=params)).get("data", [])


# ── Status-name resolution (cached per user/status_name) ────────────────────


async def resolve_status_ids(user_id: int, status_name: str) -> list[str]:
    """Return EVERY customStatusId whose name matches across every workflow.

    Wrike workspaces can have multiple workflows (often one per folder/project),
    each with its own "New", "In Progress", etc. — so a name like "New" maps to
    multiple IDs. Filtering by only one of them silently hides tasks in other
    workflows.
    """
    name = status_name.strip().lower()

    async with session_scope() as session:
        cached_rows = (
            await session.exec(
                select(WorkflowStatusCache).where(
                    WorkflowStatusCache.user_id == user_id,
                    WorkflowStatusCache.status_name == name,
                )
            )
        ).all()
        if cached_rows:
            return [r.custom_status_id for r in cached_rows]

    client = await WrikeClient.for_user(user_id)
    workflows = await client.workflows()
    matches: list[tuple[str, str]] = []  # (status_id, workflow_id)
    for wf in workflows:
        for cs in wf.get("customStatuses", []):
            if cs.get("name", "").strip().lower() == name and not cs.get("hidden"):
                matches.append((cs["id"], wf["id"]))

    if matches:
        async with session_scope() as session:
            for sid, wfid in matches:
                session.add(
                    WorkflowStatusCache(
                        user_id=user_id,
                        status_name=name,
                        custom_status_id=sid,
                        workflow_id=wfid,
                    )
                )
        logger.info(
            f"Wrike status {status_name!r} resolved to {len(matches)} IDs for user {user_id}"
        )
    return [sid for sid, _ in matches]


# Backwards-compat shim — returns the first matching ID (or None).
async def resolve_status_id(user_id: int, status_name: str) -> str | None:
    ids = await resolve_status_ids(user_id, status_name)
    return ids[0] if ids else None


def task_permalink(task: dict) -> str:
    """Best-effort task URL."""
    perma = task.get("permalink")
    if perma:
        return perma
    tid = task.get("id")
    return f"https://www.wrike.com/open.htm?id={tid}"
