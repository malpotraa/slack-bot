"""Deterministic Google Ads snapshot windows + analysis-pack aggregation."""

from datetime import date

from app.integrations.google_ads import GoogleAdsAccount
from app.integrations.google_ads_snapshot import (
    _shift_year,
    build_analysis_pack,
    compute_analysis_windows,
    pct_change,
)


def _daily(campaign_id, name, d, cost, conv, value, clicks, impr, status="ENABLED"):
    return {
        "campaign_id": campaign_id,
        "name": name,
        "channel": "SEARCH",
        "status": status,
        "date": d,
        "cost": cost,
        "conversions": conv,
        "conversion_value": value,
        "clicks": clicks,
        "impressions": impr,
    }


# ── Date windows ───────────────────────────────────────────────────────────


def test_compute_analysis_windows_anchors_on_yesterday():
    w = compute_analysis_windows("UTC", today=date(2026, 5, 20))
    assert w.as_of == date(2026, 5, 19)
    assert (w.last_7.start, w.last_7.end) == (date(2026, 5, 13), date(2026, 5, 19))
    assert (w.prior_7.start, w.prior_7.end) == (date(2026, 5, 6), date(2026, 5, 12))
    assert (w.last_30.start, w.last_30.end) == (date(2026, 4, 20), date(2026, 5, 19))
    assert (w.prior_30.start, w.prior_30.end) == (
        date(2026, 3, 21),
        date(2026, 4, 19),
    )
    assert (w.last_90.start, w.last_90.end) == (date(2026, 2, 19), date(2026, 5, 19))


def test_compute_analysis_windows_yoy_is_one_year_back():
    w = compute_analysis_windows("UTC", today=date(2026, 5, 20))
    assert (w.yoy_last_30.start, w.yoy_last_30.end) == (
        date(2025, 4, 20),
        date(2025, 5, 19),
    )


def test_compute_analysis_windows_unknown_tz_does_not_raise():
    w = compute_analysis_windows("Not/AZone", today=date(2026, 5, 20))
    assert w.as_of == date(2026, 5, 19)


def test_shift_year_handles_leap_day():
    assert _shift_year(date(2026, 3, 1)) == date(2025, 3, 1)
    assert _shift_year(date(2024, 2, 29)) == date(2023, 2, 28)


def test_pct_change():
    assert pct_change(150, 30) == 400.0
    assert pct_change(100, 50) == 100.0
    assert pct_change(10, 0) is None
    assert pct_change(None, 5) is None


# ── Analysis pack aggregation ──────────────────────────────────────────────


def _pack():
    account = GoogleAdsAccount(
        customer_id="1234567890", name="Jump", time_zone="UTC", currency_code="USD"
    )
    windows = compute_analysis_windows("UTC", today=date(2026, 5, 20))
    daily = [
        # in last_7 + last_30 + last_90
        _daily("1", "Brand", "2026-05-19", 100.0, 5.0, 500.0, 50, 1000),
        # in prior_7 + last_30 + last_90 (not last_7)
        _daily("1", "Brand", "2026-05-10", 50.0, 2.0, 200.0, 25, 500),
        # in prior_30 + last_90 only
        _daily("1", "Brand", "2026-04-01", 30.0, 1.0, 90.0, 10, 200),
    ]
    yoy_daily = [
        _daily("1", "Brand", "2025-05-01", 60.0, 3.0, 240.0, 30, 600),
    ]
    is_rows = [
        {
            "campaign_id": "1",
            "name": "Brand",
            "channel": "SEARCH",
            "status": "ENABLED",
            "budget_amount": 40.0,
            "cost": 150.0,
            "search_impression_share": 0.6,
            "search_lost_is_budget": 0.10,
            "search_lost_is_rank": 0.3,
            "search_top_is": 0.5,
            "search_abs_top_is": 0.2,
        }
    ]
    return build_analysis_pack(account, windows, daily, yoy_daily, is_rows)


def test_account_totals_aggregate_by_window():
    pack = _pack()
    totals = pack["account_totals"]
    assert totals["last_7"]["cost"] == 100.0
    assert totals["last_30"]["cost"] == 150.0
    assert totals["prior_30"]["cost"] == 30.0
    assert totals["last_90"]["cost"] == 180.0
    assert totals["yoy_last_30"]["cost"] == 60.0


def test_account_deltas_use_precomputed_windows():
    pack = _pack()
    deltas = pack["account_deltas"]
    assert deltas["wow_7d"]["cost_pct"] == 100.0  # 100 vs 50
    assert deltas["mom_30d"]["cost_pct"] == 400.0  # 150 vs 30
    assert deltas["yoy_30d"]["cost_pct"] == 150.0  # 150 vs 60


def test_campaign_row_derives_ratios_and_budget():
    pack = _pack()
    assert pack["campaign_count"] == 1
    camp = pack["campaigns"][0]
    assert camp["last_7"]["cost"] == 100.0
    assert camp["prior_7"]["cost"] == 50.0
    assert camp["last_30"]["cost"] == 150.0
    assert camp["last_30"]["conversions"] == 7.0
    assert camp["last_30"]["roas"] == round(700 / 150, 2)
    assert camp["last_30"]["cpa"] == round(150 / 7, 2)
    assert camp["impression_share"]["search_is"] == 0.6
    assert camp["budget"]["daily_budget"] == 40.0
    # lost-IS-to-budget of 10% trips the budget-limited flag.
    assert camp["budget"]["budget_limited"] is True


def test_campaign_without_impression_share_row_has_nulls():
    account = GoogleAdsAccount(
        customer_id="1234567890", name="Jump", time_zone="UTC", currency_code="USD"
    )
    windows = compute_analysis_windows("UTC", today=date(2026, 5, 20))
    daily = [_daily("9", "No-IS", "2026-05-19", 10.0, 1.0, 20.0, 5, 100)]
    pack = build_analysis_pack(account, windows, daily, [], [])
    camp = pack["campaigns"][0]
    assert camp["impression_share"] is None
    assert camp["budget"] is None


def test_day_of_week_summary_present():
    pack = _pack()
    dow = pack["day_of_week_avg"]
    # 2026-05-19 is a Tuesday.
    assert "Tuesday" in dow
    assert dow["Tuesday"]["avg_cost"] == 100.0


def test_by_channel_rollup_groups_campaigns():
    pack = _pack()
    assert set(pack["by_channel"]) == {"SEARCH"}
    search = pack["by_channel"]["SEARCH"]
    assert search["campaign_count"] == 1
    assert search["last_30"]["cost"] == 150.0


def test_daily_trend_carries_clicks_and_impressions():
    # Charts derive CPC / conv-rate / CTR per day, so the trend needs both.
    pack = _pack()
    assert pack["daily_trend_90d"]
    sample = pack["daily_trend_90d"][-1]
    assert {"date", "cost", "conversions", "clicks", "impressions"} <= set(sample)


def test_snapshot_carries_per_campaign_daily_series():
    # Tool-internal — feeds per-campaign trend charts.
    pack = _pack()
    series = pack["daily_by_campaign"]["1"]
    assert series
    assert all("date" in r and "cost" in r and "conversion_value" in r for r in series)
