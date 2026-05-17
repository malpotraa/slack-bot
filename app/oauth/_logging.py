"""Shared safe-log helpers for OAuth modules.

Goal: surface enough detail to debug a failure without leaking the request
body / credentials echoed by some providers in error responses.
"""

from __future__ import annotations

import httpx


def safe_error_summary(resp: httpx.Response) -> str:
    """Extract a short, redaction-safe error summary from an OAuth response.

    Prefers `error` + `error_description` from JSON bodies. Falls back to
    HTTP status. Never returns raw response text.
    """
    try:
        data = resp.json()
        if isinstance(data, dict):
            err = data.get("error") or data.get("error_code") or "unknown_error"
            desc = data.get("error_description") or data.get("errorDescription") or ""
            # Cap desc length so a provider can't drown the log even if it tries.
            desc = (desc or "")[:200]
            return f"status={resp.status_code} error={err!r} desc={desc!r}"
    except Exception:
        pass
    return f"status={resp.status_code} (non-JSON body, omitted)"
