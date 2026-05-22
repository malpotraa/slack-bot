"""Wide Google Ads account snapshot + deterministic analysis (the surface tier).

One generous pull per thread answers most account-level questions: 90 days of
campaign-level daily metrics, the same 30-day window one year ago, and
per-campaign impression share + budgets. Campaigns are grouped by advertising
channel type so the snapshot covers Search, Shopping, Performance Max, Demand
Gen, Video and Display in one shape.

Every comparison (WoW, MoM, YoY, day-of-week, trend) is a deterministic
re-aggregation here — the LLM only narrates it, never computes.

The snapshot is cached in-process per Slack thread (Cloud Run runs a single
instance for Socket Mode); follow-up questions and drill-downs reuse it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from app.integrations.google_ads import DateWindow, GoogleAdsAccount, GoogleAdsClient
from app.utils.cache import KeyedLocks, TTLCache

# Cloud Run is pinned to one instance for Socket Mode, so an in-process cache is
# consistent. Ads data lags hours, so 20 minutes of staleness is fine; a restart
# just re-pulls on the next question. The per-key locks are singleflight — two
# concurrent misses for the same thread share one pull.
_SNAPSHOT_CACHE: TTLCache[tuple, dict[str, Any]] = TTLCache(
    maxsize=64, ttl_seconds=20 * 60
)
_SNAPSHOT_LOCKS = KeyedLocks(maxsize=128)

_TREND_DAYS = 90
_TOP_CAMPAIGNS = 25

METRIC_KEYS = (
    "cost",
    "conversions",
    "conversion_value",
    "clicks",
    "impressions",
)


# ── Shared metric helpers (also used by google_ads_drill) ──────────────────


def zero_metrics() -> dict[str, float]:
    return {k: 0.0 for k in METRIC_KEYS}


def accumulate(target: dict[str, float], row: dict[str, Any]) -> None:
    for k in METRIC_KEYS:
        target[k] += float(row.get(k) or 0)


def derive_metrics(m: dict[str, float]) -> dict[str, Any]:
    """Totals + derived ratios for one window. Ratios are None when undefined."""
    cost = float(m.get("cost") or 0)
    conv = float(m.get("conversions") or 0)
    value = float(m.get("conversion_value") or 0)
    clicks = float(m.get("clicks") or 0)
    impr = float(m.get("impressions") or 0)
    return {
        "cost": round(cost, 2),
        "conversions": round(conv, 2),
        "conversion_value": round(value, 2),
        "clicks": int(clicks),
        "impressions": int(impr),
        "cpc": round(cost / clicks, 2) if clicks else None,
        "cpa": round(cost / conv, 2) if conv else None,
        "roas": round(value / cost, 2) if cost else None,
        "conv_rate": round(conv / clicks, 4) if clicks else None,
        "ctr": round(clicks / impr, 4) if impr else None,
    }


def pct_change(cur: Any, prev: Any) -> float | None:
    if cur is None or prev is None:
        return None
    try:
        cur_f, prev_f = float(cur), float(prev)
    except (TypeError, ValueError):
        return None
    if prev_f == 0:
        return None
    return round((cur_f - prev_f) / prev_f * 100, 1)


def deltas(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    """Percent change for every metric between two derived windows."""
    return {
        f"{key}_pct": pct_change(current.get(key), previous.get(key))
        for key in (
            "cost",
            "conversions",
            "conversion_value",
            "clicks",
            "impressions",
            "cpc",
            "cpa",
            "roas",
            "conv_rate",
            "ctr",
        )
    }


# ── Date windows ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AnalysisWindows:
    as_of: date
    last_7: DateWindow
    prior_7: DateWindow
    last_30: DateWindow
    prior_30: DateWindow
    last_90: DateWindow
    yoy_last_30: DateWindow


def _shift_year(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # Feb 29 → Feb 28 last year
        return d.replace(year=d.year - 1, day=28)


def compute_analysis_windows(
    time_zone: str, *, today: date | None = None
) -> AnalysisWindows:
    """All date windows the analyst needs, anchored on the account's timezone.
    `as_of` is yesterday — today's Ads data is always incomplete.
    """
    try:
        tz = ZoneInfo(time_zone)
    except Exception:
        tz = ZoneInfo("UTC")
    account_today = today or datetime.now(tz).date()
    as_of = account_today - timedelta(days=1)

    return AnalysisWindows(
        as_of=as_of,
        last_7=DateWindow("last_7", as_of - timedelta(days=6), as_of),
        prior_7=DateWindow(
            "prior_7", as_of - timedelta(days=13), as_of - timedelta(days=7)
        ),
        last_30=DateWindow("last_30", as_of - timedelta(days=29), as_of),
        prior_30=DateWindow(
            "prior_30", as_of - timedelta(days=59), as_of - timedelta(days=30)
        ),
        last_90=DateWindow("last_90", as_of - timedelta(days=89), as_of),
        yoy_last_30=DateWindow(
            "yoy_last_30",
            _shift_year(as_of - timedelta(days=29)),
            _shift_year(as_of),
        ),
    )


def _in_window(d: str, window: DateWindow) -> bool:
    # ISO dates compare correctly as plain strings.
    return bool(d) and window.start.isoformat() <= d <= window.end.isoformat()


# ── Deterministic aggregation ──────────────────────────────────────────────


def day_of_week_summary(daily_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Average metrics by weekday for already daily-shaped rows."""
    dow_acc: dict[str, dict[str, float]] = {}
    dow_count: dict[str, int] = {}
    for row in daily_rows:
        d = row.get("date") or ""
        try:
            weekday = date.fromisoformat(d).strftime("%A")
        except ValueError:
            continue
        acc = dow_acc.setdefault(weekday, zero_metrics())
        for k in METRIC_KEYS:
            acc[k] += float(row.get(k) or 0)
        dow_count[weekday] = dow_count.get(weekday, 0) + 1

    return {
        weekday: {
            "avg_cost": round(acc["cost"] / (dow_count[weekday] or 1), 2),
            "avg_conversions": round(
                acc["conversions"] / (dow_count[weekday] or 1), 2
            ),
            "avg_conversion_value": round(
                acc["conversion_value"] / (dow_count[weekday] or 1), 2
            ),
            "days_sampled": dow_count[weekday],
        }
        for weekday, acc in dow_acc.items()
    }


def _account_daily_series(
    daily: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Account-level daily trend + average-by-weekday summary."""
    by_date: dict[str, dict[str, float]] = {}
    for row in daily:
        d = row.get("date") or ""
        if not d:
            continue
        accumulate(by_date.setdefault(d, zero_metrics()), row)

    trend = [
        {
            "date": d,
            "cost": round(by_date[d]["cost"], 2),
            "conversions": round(by_date[d]["conversions"], 2),
            "conversion_value": round(by_date[d]["conversion_value"], 2),
            "clicks": int(by_date[d]["clicks"]),
            "impressions": int(by_date[d]["impressions"]),
        }
        for d in sorted(by_date)
    ]
    return trend[-_TREND_DAYS:], day_of_week_summary(trend)


def build_analysis_pack(
    account: GoogleAdsAccount,
    windows: AnalysisWindows,
    daily: list[dict[str, Any]],
    yoy_daily: list[dict[str, Any]],
    is_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Turn raw GAQL rows into the compact, fully-computed fact pack the agent
    narrates. Pure function — easy to unit-test.
    """
    window_map: dict[str, DateWindow] = {
        "last_7": windows.last_7,
        "prior_7": windows.prior_7,
        "last_30": windows.last_30,
        "prior_30": windows.prior_30,
        "last_90": windows.last_90,
    }

    # Per-campaign per-window aggregation + per-campaign daily series (the
    # latter is tool-internal — it feeds per-campaign trend charts, never the
    # model digest).
    campaigns: dict[str, dict[str, Any]] = {}
    daily_by_campaign: dict[str, list[dict[str, Any]]] = {}
    for row in daily:
        cid = row.get("campaign_id") or ""
        if not cid:
            continue
        camp = campaigns.setdefault(
            cid,
            {
                "campaign_id": cid,
                "name": row.get("name") or f"Campaign {cid}",
                "channel": row.get("channel") or "UNKNOWN",
                "status": row.get("status") or "",
                "windows": {w: zero_metrics() for w in window_map},
            },
        )
        for key in ("name", "status", "channel"):
            if row.get(key):
                camp[key] = row[key]
        d = row.get("date") or ""
        for wname, window in window_map.items():
            if _in_window(d, window):
                accumulate(camp["windows"][wname], row)
        daily_by_campaign.setdefault(cid, []).append(
            {
                "date": d,
                "cost": round(float(row.get("cost") or 0), 2),
                "conversions": round(float(row.get("conversions") or 0), 2),
                "conversion_value": round(float(row.get("conversion_value") or 0), 2),
                "clicks": int(row.get("clicks") or 0),
                "impressions": int(row.get("impressions") or 0),
            }
        )
    for series in daily_by_campaign.values():
        series.sort(key=lambda r: r.get("date") or "")

    # Year-ago aggregation by campaign id.
    yoy: dict[str, dict[str, float]] = {}
    for row in yoy_daily:
        cid = row.get("campaign_id") or ""
        if cid:
            accumulate(yoy.setdefault(cid, zero_metrics()), row)

    is_map = {r.get("campaign_id"): r for r in is_rows if r.get("campaign_id")}

    # Account totals per window.
    account_totals: dict[str, Any] = {}
    for wname in window_map:
        total = zero_metrics()
        for camp in campaigns.values():
            for k in METRIC_KEYS:
                total[k] += camp["windows"][wname][k]
        account_totals[wname] = derive_metrics(total)
    yoy_total = zero_metrics()
    for metrics in yoy.values():
        for k in METRIC_KEYS:
            yoy_total[k] += metrics[k]
    account_totals["yoy_last_30"] = derive_metrics(yoy_total)

    # Per-campaign output rows.
    campaign_rows: list[dict[str, Any]] = []
    for cid, camp in campaigns.items():
        last_7 = derive_metrics(camp["windows"]["last_7"])
        last_30 = derive_metrics(camp["windows"]["last_30"])
        prior_30 = derive_metrics(camp["windows"]["prior_30"])
        is_row = is_map.get(cid) or {}

        budget_amount = is_row.get("budget_amount") or None
        avg_daily_spend = round((camp["windows"]["last_7"]["cost"] or 0) / 7, 2)
        budget_block: dict[str, Any] | None = None
        if budget_amount:
            pacing = round(avg_daily_spend / budget_amount * 100, 1)
            lost_budget = is_row.get("search_lost_is_budget")
            budget_block = {
                "daily_budget": round(budget_amount, 2),
                "avg_daily_spend_last_7": avg_daily_spend,
                "pacing_pct": pacing,
                "budget_limited": bool(
                    (lost_budget is not None and lost_budget >= 0.05)
                    or pacing >= 90
                ),
            }

        impression_share: dict[str, Any] | None = None
        if is_row:
            impression_share = {
                "search_is": is_row.get("search_impression_share"),
                "lost_is_budget": is_row.get("search_lost_is_budget"),
                "lost_is_rank": is_row.get("search_lost_is_rank"),
                "top_is": is_row.get("search_top_is"),
                "abs_top_is": is_row.get("search_abs_top_is"),
            }

        campaign_rows.append(
            {
                "campaign_id": cid,
                "name": camp["name"],
                "channel": camp["channel"],
                "status": camp["status"],
                "last_7": last_7,
                "prior_7": derive_metrics(camp["windows"]["prior_7"]),
                "last_30": last_30,
                "prior_30": prior_30,
                "last_90": derive_metrics(camp["windows"]["last_90"]),
                "yoy_last_30": derive_metrics(yoy.get(cid, zero_metrics())),
                "delta_30d_vs_prior_30": deltas(last_30, prior_30),
                "impression_share": impression_share,
                "budget": budget_block,
            }
        )

    campaign_rows.sort(key=lambda c: -(c["last_30"]["cost"] or 0))

    # Per-channel-type rollup so the surface answer can be channel-aware.
    by_channel: dict[str, Any] = {}
    channel_groups: dict[str, list[dict[str, Any]]] = {}
    for camp in campaign_rows:
        channel_groups.setdefault(camp["channel"] or "UNKNOWN", []).append(camp)
    for channel, rows in channel_groups.items():
        last_30 = zero_metrics()
        prior_30 = zero_metrics()
        for camp in rows:
            for k in METRIC_KEYS:
                last_30[k] += camp["last_30"][k] or 0
                prior_30[k] += camp["prior_30"][k] or 0
        derived_last_30 = derive_metrics(last_30)
        derived_prior_30 = derive_metrics(prior_30)
        by_channel[channel] = {
            "campaign_count": len(rows),
            "last_30": derived_last_30,
            "prior_30": derived_prior_30,
            "delta_30d_vs_prior_30": deltas(derived_last_30, derived_prior_30),
        }

    shown = campaign_rows[:_TOP_CAMPAIGNS]
    tail = campaign_rows[_TOP_CAMPAIGNS:]
    other_campaigns: dict[str, Any] | None = None
    if tail:
        other_campaigns = {
            "count": len(tail),
            "last_30_cost": round(sum(c["last_30"]["cost"] or 0 for c in tail), 2),
            "last_30_conversions": round(
                sum(c["last_30"]["conversions"] or 0 for c in tail), 2
            ),
        }

    daily_trend, day_of_week = _account_daily_series(daily)

    return {
        "account": {
            "name": account.name,
            "customer_id": account.customer_id,
            "currency": account.currency_code,
            "time_zone": account.time_zone,
        },
        "as_of": windows.as_of.isoformat(),
        "windows": {
            name: getattr(windows, name).as_dict()
            for name in (
                "last_7",
                "prior_7",
                "last_30",
                "prior_30",
                "last_90",
                "yoy_last_30",
            )
        },
        "account_totals": account_totals,
        "account_deltas": {
            "wow_7d": deltas(account_totals["last_7"], account_totals["prior_7"]),
            "mom_30d": deltas(
                account_totals["last_30"], account_totals["prior_30"]
            ),
            "yoy_30d": deltas(
                account_totals["last_30"], account_totals["yoy_last_30"]
            ),
        },
        "by_channel": by_channel,
        "campaign_count": len(campaign_rows),
        "campaigns": shown,
        "other_campaigns": other_campaigns,
        "daily_trend_90d": daily_trend,
        # Per-campaign daily series — tool-internal, feeds per-campaign trend
        # charts. Never included in the model digest.
        "daily_by_campaign": daily_by_campaign,
        "day_of_week_avg": day_of_week,
        "notes": [
            "All figures exclude today; data runs through `as_of`.",
            "Impression-share fields apply to Search/Shopping campaigns and "
            "are null elsewhere.",
            "Money values are in the account currency.",
            "Campaigns are sorted by last-30-day cost; a long tail may be "
            "rolled into `other_campaigns`.",
        ],
    }


# ── Fetch + per-thread cache ───────────────────────────────────────────────


async def _fetch_snapshot(user_id: int, account: GoogleAdsAccount) -> dict[str, Any]:
    client = await GoogleAdsClient.for_user(user_id)
    windows = compute_analysis_windows(account.time_zone)

    results = await asyncio.gather(
        client.campaign_daily_metrics(account.customer_id, windows.last_90),
        client.campaign_daily_metrics(account.customer_id, windows.yoy_last_30),
        client.campaign_impression_share(account.customer_id, windows.last_30),
        return_exceptions=True,
    )
    daily, yoy_daily, is_rows = results
    if isinstance(daily, BaseException):
        raise daily  # the 90-day pull is essential
    if isinstance(yoy_daily, BaseException):
        logger.warning(f"Google Ads year-ago pull failed: {yoy_daily}")
        yoy_daily = []
    if isinstance(is_rows, BaseException):
        logger.warning(f"Google Ads impression-share pull failed: {is_rows}")
        is_rows = []

    return build_analysis_pack(account, windows, daily, yoy_daily, is_rows)


async def get_or_build_snapshot(
    *, user_id: int, account: GoogleAdsAccount, cache_key: tuple
) -> dict[str, Any]:
    """Return a cached snapshot for the thread, or pull a fresh one. A per-key
    lock means two concurrent misses don't both run the 90-day pull."""
    cached = _SNAPSHOT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    async with _SNAPSHOT_LOCKS.get(cache_key):
        cached = _SNAPSHOT_CACHE.get(cache_key)  # re-check inside the lock
        if cached is not None:
            return cached
        pack = await _fetch_snapshot(user_id, account)
        _SNAPSHOT_CACHE.set(cache_key, pack)
        return pack
