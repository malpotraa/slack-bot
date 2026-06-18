"""Braintrust dataset capture for the Google Ads analyst.

Called (via asyncio.to_thread) at the end of every successful Google Ads agent
turn when EVAL_CAPTURE=true. Each row stored is the minimum needed to run the
four scorers — we never store absolute spend / campaign names in Braintrust;
only percentage/rate fields needed to verify the reply and the full reply.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.config import settings


# ── Delta extraction ───────────────────────────────────────────────────────


def _filter_pct(obj: Any) -> Any:
    """Recursively keep only *_pct numeric leaf values; drop everything else.

    The result is a same-shaped skeleton of the fact_pack with only percentage
    deltas — no absolute spend, no campaign names, no account IDs.
    """
    if isinstance(obj, dict):
        out: dict = {}
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                sub = _filter_pct(v)
                if sub:
                    out[k] = sub
            elif k.endswith("_pct") and isinstance(v, (int, float)):
                out[k] = float(v)
        return out
    if isinstance(obj, list):
        items = [_filter_pct(i) for i in obj if isinstance(i, (dict, list))]
        return [i for i in items if i]
    return {}


def extract_deltas(fact_pack: dict) -> dict:
    """Return a *_pct-only skeleton of a fact_pack — safe to store externally."""
    return _filter_pct(fact_pack)


_TREND_KEYS = {
    "metric",
    "days",
    "start",
    "end",
    "first_daily_value",
    "last_daily_value",
    "min_daily_value",
    "max_daily_value",
    "earlier_period_value",
    "recent_period_value",
    "period_value_basis",
    "recent_vs_earlier_pct",
    "direction",
}
_TREND_CONTAINERS = {"account_trend", "campaign_trend", "trend_90d"}


def _filter_trends(obj: Any, *, key_name: str = "") -> Any:
    """Keep compact trend summaries without account/campaign identifiers."""
    if isinstance(obj, dict):
        if key_name in _TREND_CONTAINERS:
            if all(isinstance(v, dict) for v in obj.values()):
                nested = {
                    k: _filter_trends(v, key_name=key_name)
                    for k, v in obj.items()
                    if isinstance(v, dict)
                }
                return {k: v for k, v in nested.items() if v}
            return {k: v for k, v in obj.items() if k in _TREND_KEYS}

        out: dict = {}
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                sub = _filter_trends(v, key_name=k)
                if sub:
                    out[k] = sub
        return out
    if isinstance(obj, list):
        items = [_filter_trends(i, key_name=key_name) for i in obj if isinstance(i, dict)]
        return [i for i in items if i]
    return {}


def extract_trends(fact_pack: dict) -> dict:
    """Return trend summaries only — no absolute spend or campaign names."""
    return _filter_trends(fact_pack)


# ── Dataset insertion ──────────────────────────────────────────────────────


def capture_ads_turn(
    *,
    user_message: str,
    reply: str,
    fact_pack: dict,
    tool_name: str,
    tool_args: dict,
    prompt_version: str,
    cost_usd: float,
    thread_ts: str,
) -> None:
    """Insert one Google Ads agent turn into the Braintrust eval dataset.

    Synchronous — call via asyncio.to_thread from the async agent runner.
    Silently no-ops when braintrust is not importable or the key is missing.
    """
    if not settings.eval_capture or not settings.braintrust_api_key:
        return

    try:
        import braintrust
    except ImportError:
        logger.warning("braintrust not installed; eval capture skipped")
        return

    try:
        dataset = braintrust.init_dataset(
            project=settings.braintrust_project,
            name="ads-analyst-turns",
        )
        dataset.insert(
            input={
                "message": user_message,
                "tool_args": {
                    "tool_name": tool_name,
                    "scope": tool_args.get("scope"),
                    "metric": tool_args.get("metric"),
                    "extras": tool_args.get("extras") or [],
                    "customer_id": tool_args.get("customer_id"),
                    "campaign_id": tool_args.get("campaign_id"),
                    "include_charts": bool(tool_args.get("include_charts")),
                    "chart_metrics": tool_args.get("chart_metrics") or [],
                    "chart_days": tool_args.get("chart_days"),
                    "date_from": tool_args.get("date_from"),
                    "date_to": tool_args.get("date_to"),
                },
            },
            output=reply,
            metadata={
                # Only percentage/rate diagnostics — no absolute spend or names.
                "fact_pack_deltas": extract_deltas(fact_pack),
                "fact_pack_trends": extract_trends(fact_pack),
                "prompt_version": prompt_version,
                "cost_usd": cost_usd,
                "thread_ts": thread_ts,
            },
        )
        dataset.flush()
        logger.debug(f"eval row captured (thread={thread_ts})")
    except Exception as exc:
        logger.warning(f"eval dataset insert failed: {exc}")
