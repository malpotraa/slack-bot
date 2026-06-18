"""Slim, labeled model-facing digests for Google Ads fact packs."""

from app.agent.tools import (
    _campaign_chart_specs,
    _drill_chart_specs,
    _drill_digest,
    _get_google_ads_data,
    _overview_digest,
)
from app.integrations.google_ads import GoogleAdsAccount
from app.integrations.google_ads_snapshot import derive_metrics


def _m(cost=1000.0, conv=50.0, value=5000.0):
    return derive_metrics(
        {
            "cost": cost,
            "conversions": conv,
            "conversion_value": value,
            "clicks": 500,
            "impressions": 10000,
        }
    )


def _snapshot():
    return {
        "account": {"name": "Acme", "customer_id": "123", "currency": "USD"},
        "as_of": "2026-05-20",
        "windows": {"last_30": {"start": "2026-04-21", "end": "2026-05-20"}},
        "account_totals": {
            "last_7": _m(300, 12, 1200),
            "prior_7": _m(250, 10, 1000),
            "last_30": _m(),
            "prior_30": _m(800, 40, 4000),
            "last_90": _m(3000, 120, 12000),
            "yoy_last_30": _m(700, 35, 3500),
        },
        "account_deltas": {
            "wow_7d": {"cost_pct": 20.0, "conversion_value_pct": 99.0},
            "mom_30d": {
                "cost_pct": 25.0,
                "conversion_value_pct": 99.0,
                "cpa_pct": -5.0,
            },
            "yoy_30d": {"cost_pct": 42.0, "conversion_value_pct": 99.0},
        },
        "by_channel": {
            "SEARCH": {
                "campaign_count": 1,
                "last_30": _m(),
                "prior_30": _m(),
                "delta_30d_vs_prior_30": {},
            }
        },
        "campaign_count": 1,
        "campaigns": [
            {
                "name": "Brand",
                "campaign_id": "7",
                "channel": "SEARCH",
                "status": "ENABLED",
                "last_7": _m(300, 12, 1200),
                "prior_7": _m(200, 8, 800),
                "last_30": _m(),
                "yoy_last_30": _m(700, 35, 3500),
                "delta_30d_vs_prior_30": {
                    "cost_pct": 10.0,
                    "cpa_pct": -5.0,
                    "roas_pct": 8.0,
                    "conversions_pct": 12.0,
                },
                "budget": {"pacing_pct": 80.0, "budget_limited": False},
                "impression_share": {"lost_is_budget": 0.05, "lost_is_rank": 0.2},
            }
        ],
        "other_campaigns": None,
        "day_of_week_avg": {"Monday": {"avg_cost": 33.0}},
        "daily_trend_90d": [{"date": "2026-05-20", "cost": 100}] * 90,
        "daily_by_campaign": {
            "7": [
                {
                    "date": "2026-05-19",
                    "cost": 100.0,
                    "conversions": 5.0,
                    "clicks": 50,
                    "impressions": 1000,
                },
                {
                    "date": "2026-05-20",
                    "cost": 200.0,
                    "conversions": 10.0,
                    "clicks": 100,
                    "impressions": 2000,
                },
            ]
        },
    }


class _FakeAdsClient:
    async def account_by_id(self, customer_id):
        return GoogleAdsAccount(
            customer_id=customer_id,
            name="Acme",
            time_zone="UTC",
            currency_code="USD",
        )


def test_overview_digest_drops_the_daily_trend():
    d = _overview_digest(_snapshot(), "cost_per_conv")
    assert "daily_trend_90d" not in d  # the 90-row series is not sent to the model
    assert "daily_by_campaign" not in d  # nor the per-campaign series
    assert "day_of_week_avg" not in d


def test_overview_digest_carries_per_campaign_trend_deltas():
    # So "metric + trend for campaign X" answers without a drill.
    d = _overview_digest(_snapshot(), "cost_per_conv")
    for delta_field in ("cost_pct", "conv_rate_pct", "ctr_pct", "cpc_pct"):
        assert delta_field in d["campaigns"][0]


def test_overview_digest_campaigns_are_labeled_dicts():
    d = _overview_digest(_snapshot(), "cost_per_conv")
    assert isinstance(d["campaigns"][0], dict)
    assert d["campaigns"][0]["name"] == "Brand"
    assert d["campaigns"][0]["conv_rate"] == 0.1


def test_overview_digest_default_metric_omits_roas_and_conv_value():
    d = _overview_digest(_snapshot(), "cost_per_conv")
    assert "cost_per_conv" in d["campaigns"][0]
    assert "roas" not in d["campaigns"][0]
    last_30 = d["account_totals"]["last_30"]
    assert "cost_per_conv" in last_30
    assert "roas" not in last_30
    assert "conversion_value" not in last_30
    assert "conversion_value_pct" not in d["account_deltas"]["mom_30d"]
    assert set(d["account_totals"]) == {"last_30"}
    assert set(d["account_deltas"]) == {"mom_30d"}


def test_overview_digest_adds_only_requested_extra_slices():
    d = _overview_digest(
        _snapshot(),
        "cost_per_conv",
        extras={"wow", "yoy", "day_of_week", "90d"},
    )
    assert set(d["account_totals"]) == {"last_30", "last_7"}
    assert set(d["account_deltas"]) == {"mom_30d", "wow_7d", "yoy_30d"}
    assert "conversion_value_pct" not in d["account_deltas"]["wow_7d"]
    assert d["day_of_week_avg"] == {"Monday": {"avg_cost": 33.0}}
    assert d["trend_90d"]["cost"]["days"] == 90
    assert "daily_trend_90d" not in d


def test_overview_digest_roas_mode_swaps_the_metric():
    d = _overview_digest(_snapshot(), "roas")
    assert "roas" in d["campaigns"][0]
    assert "cost_per_conv" not in d["campaigns"][0]
    assert "roas" in d["account_totals"]["last_30"]


def test_drill_digest_labeled_rows_with_graceful_errors():
    drill = {
        "account": {"name": "Acme", "currency": "USD"},
        "campaign": {
            "name": "Brand",
            "channel": "SEARCH",
            "status": "ENABLED",
            "last_30": _m(),
            "delta_30d_vs_prior_30": {},
        },
        "window": {"start": "2026-04-21", "end": "2026-05-20"},
        "breakdown": {
            "keywords": {"rows": [dict(_m(), label="kw1"), dict(_m(), label="kw2")]},
            "ad_groups": {"error": "boom"},
        },
    }
    d = _drill_digest(drill, "cost_per_conv")
    assert isinstance(d["breakdown"]["keywords"][0], dict)
    assert d["breakdown"]["keywords"][0]["cost_per_conv"] == 20.0
    assert d["breakdown"]["ad_groups"] == {"error": "boom"}


def test_overview_digest_selected_campaign_trend_is_summarized_not_raw():
    snap = _snapshot()
    snap["campaigns"][0]["campaign_id"] = "7"
    snap["daily_by_campaign"] = {
        "7": [
            {
                "date": f"2026-05-{d:02d}",
                "cost": 100.0,
                "conversions": float(d),
                "clicks": 100,
                "impressions": 1000,
            }
            for d in range(1, 11)
        ]
    }
    d = _overview_digest(
        snap,
        "cost_per_conv",
        selected_campaign=snap["campaigns"][0],
        trend_metric="conv_rate",
        chart_days=10,
    )
    assert d["kind"] == "selected_campaign"
    assert d["campaign"]["name"] == "Brand"
    assert d["campaign_trend"]["metric"] == "conv_rate"
    assert d["campaign_trend"]["direction"] == "up"
    assert "daily_by_campaign" not in d


def test_overview_digest_account_trend_uses_weighted_rate_math():
    snap = _snapshot()
    snap["daily_trend_90d"] = [
        {
            "date": "2026-05-01",
            "cost": 10.0,
            "conversions": 1.0,
            "clicks": 1,
            "impressions": 100,
        },
        {
            "date": "2026-05-02",
            "cost": 10.0,
            "conversions": 0.0,
            "clicks": 99,
            "impressions": 100,
        },
        {
            "date": "2026-05-03",
            "cost": 10.0,
            "conversions": 10.0,
            "clicks": 100,
            "impressions": 100,
        },
        {
            "date": "2026-05-04",
            "cost": 10.0,
            "conversions": 10.0,
            "clicks": 100,
            "impressions": 100,
        },
    ]

    d = _overview_digest(
        snap,
        "cost_per_conv",
        trend_metric="conv_rate",
        chart_days=4,
    )

    trend = d["account_trend"]
    assert trend["metric"] == "conv_rate"
    assert trend["period_value_basis"] == "weighted_by_clicks"
    assert trend["earlier_period_value"] == 0.01
    assert trend["recent_period_value"] == 0.1
    assert trend["direction"] == "up"


def test_overview_digest_selected_campaign_excludes_unneeded_overview_sections():
    snap = _snapshot()
    campaigns = []
    for i in range(13):
        campaigns.append(
            {
                **snap["campaigns"][0],
                "name": f"Campaign {i}",
                "last_30": _m(cost=1000 - i, conv=10),
            }
        )
    snap["campaigns"] = campaigns
    d = _overview_digest(
        snap,
        "cost_per_conv",
        selected_campaign=campaigns[12],
    )
    assert d["campaign"]["name"] == "Campaign 12"
    assert "campaigns" not in d
    assert "by_channel" not in d
    assert "account_totals" not in d


def test_selected_campaign_digest_can_include_requested_extras():
    d = _overview_digest(
        _snapshot(),
        "cost_per_conv",
        selected_campaign=_snapshot()["campaigns"][0],
        extras={"wow", "yoy", "day_of_week", "90d"},
    )
    assert d["kind"] == "selected_campaign"
    assert "campaigns" not in d
    assert "account_deltas" not in d
    assert d["campaign"]["last_7"]["cost"] == 300.0
    assert d["campaign"]["delta_7d_vs_prior_7"]["cost_pct"] == 50.0
    assert d["campaign"]["delta_30d_vs_yoy"]["cost_pct"] == 42.9
    assert d["campaign"]["day_of_week_avg"]["Tuesday"]["avg_cost"] == 100.0
    assert d["campaign"]["day_of_week_avg"]["Wednesday"]["avg_cost"] == 200.0
    assert d["campaign"]["trend_90d"]["cost"]["days"] == 2


def test_overview_digest_account_overview_caps_campaign_rows():
    snap = _snapshot()
    snap["campaigns"] = [
        {**snap["campaigns"][0], "name": f"Campaign {i}"}
        for i in range(12)
    ]
    d = _overview_digest(snap, "cost_per_conv")
    assert len(d["campaigns"]) == 8
    assert d["campaigns"][-1]["name"] == "Campaign 7"


# ── Chart specs ────────────────────────────────────────────────────────────


def test_campaign_chart_specs_builds_a_line_from_the_daily_series():
    snap = {
        "daily_by_campaign": {
            "7": [
                {
                    "date": f"2026-05-{d:02d}",
                    "cost": d * 10.0,
                    "conversions": float(d),
                    "clicks": d * 5,
                    "impressions": d * 100,
                }
                for d in range(1, 11)
            ]
        }
    }
    specs = _campaign_chart_specs(
        snap, {"campaign_id": "7", "name": "Brand"}, ["conv_rate"], 7
    )
    assert len(specs) == 1
    assert specs[0]["kind"] == "line"
    assert len(specs[0]["x"]) == 7  # last 7 of 10 days
    assert "Brand" in specs[0]["title"]


def test_campaign_chart_specs_empty_when_campaign_has_no_series():
    specs = _campaign_chart_specs(
        {"daily_by_campaign": {}}, {"campaign_id": "9"}, ["cost"], 30
    )
    assert specs == []


def test_drill_chart_specs_honors_the_requested_metric():
    drill = {
        "account": {"currency": "USD"},
        "breakdown": {
            "keywords": {
                "rows": [
                    {"label": "a", "cost": 100, "conv_rate": 0.10},
                    {"label": "b", "cost": 50, "conv_rate": 0.30},
                ]
            }
        },
    }
    specs = _drill_chart_specs(drill, ["conv_rate"])
    assert specs and specs[0]["kind"] == "bar"
    assert specs[0]["value_kind"] == "percent"
    assert specs[0]["labels"][0] == "b"  # ranked by conv_rate desc


# ── get_google_ads_data chart behavior ─────────────────────────────────────


async def test_campaign_chart_request_suppresses_account_table(monkeypatch):
    snap = _snapshot()
    snap["campaigns"][0]["campaign_id"] = "7"
    snap["daily_by_campaign"] = {
        "7": [
            {
                "date": f"2026-05-{d:02d}",
                "cost": 100.0,
                "conversions": float(d),
                "clicks": 100,
                "impressions": 1000,
            }
            for d in range(1, 11)
        ]
    }

    async def fake_for_user(_user_id):
        return _FakeAdsClient()

    async def fake_snapshot(**_kwargs):
        return snap

    async def fake_render(_specs):
        return [{"filename": "conv_rate.png", "png": b"png", "title": "chart"}]

    monkeypatch.setattr("app.agent.tools.google_ads.GoogleAdsClient.for_user", fake_for_user)
    monkeypatch.setattr("app.agent.tools.google_ads_snapshot.get_or_build_snapshot", fake_snapshot)
    monkeypatch.setattr("app.agent.tools._render_specs", fake_render)

    out = await _get_google_ads_data(
        {
            "customer_id": "123",
            "scope": "account_overview",
            "campaign_name": "Brand",
            "include_charts": True,
            "chart_metrics": ["conv_rate"],
        },
        user_id=1,
        channel_id="C",
        thread_ts="T",
    )

    assert out["kind"] == "campaign_chart"
    assert out["blocks"] is None
    assert out["images"]
    assert out["fact_pack"]["campaign"]["name"] == "Brand"
    assert out["fact_pack"]["campaign_trend"]["metric"] == "conv_rate"


async def test_account_chart_request_suppresses_account_table_and_adds_trend(monkeypatch):
    snap = _snapshot()
    snap["daily_trend_90d"] = [
        {
            "date": f"2026-05-{d:02d}",
            "cost": 100.0,
            "conversions": float(d),
            "clicks": 100,
            "impressions": 1000,
        }
        for d in range(1, 11)
    ]

    async def fake_for_user(_user_id):
        return _FakeAdsClient()

    async def fake_snapshot(**_kwargs):
        return snap

    async def fake_render(_specs):
        return [{"filename": "conv_rate.png", "png": b"png", "title": "chart"}]

    monkeypatch.setattr("app.agent.tools.google_ads.GoogleAdsClient.for_user", fake_for_user)
    monkeypatch.setattr("app.agent.tools.google_ads_snapshot.get_or_build_snapshot", fake_snapshot)
    monkeypatch.setattr("app.agent.tools._render_specs", fake_render)

    out = await _get_google_ads_data(
        {
            "customer_id": "123",
            "scope": "account_overview",
            "include_charts": True,
            "chart_metrics": ["conv_rate"],
            "chart_days": 10,
        },
        user_id=1,
        channel_id="C",
        thread_ts="T",
    )

    assert out["kind"] == "account_overview"
    assert out["blocks"] is None
    assert out["images"]
    assert out["fact_pack"]["account_trend"]["metric"] == "conv_rate"


async def test_campaign_metric_request_suppresses_account_table(monkeypatch):
    snap = _snapshot()
    snap["campaigns"][0]["campaign_id"] = "7"

    async def fake_for_user(_user_id):
        return _FakeAdsClient()

    async def fake_snapshot(**_kwargs):
        return snap

    monkeypatch.setattr("app.agent.tools.google_ads.GoogleAdsClient.for_user", fake_for_user)
    monkeypatch.setattr("app.agent.tools.google_ads_snapshot.get_or_build_snapshot", fake_snapshot)

    out = await _get_google_ads_data(
        {
            "customer_id": "123",
            "scope": "account_overview",
            "campaign_name": "Brand",
        },
        user_id=1,
        channel_id="C",
        thread_ts="T",
    )

    assert out["kind"] == "campaign_metric"
    assert out["blocks"] is None
    assert out["images"] == []
    assert out["_resolved_campaign_name"] == "Brand"


async def test_campaign_chart_request_does_not_fall_back_to_account_chart(monkeypatch):
    snap = _snapshot()
    snap["campaigns"] = [
        {**snap["campaigns"][0], "campaign_id": "1", "name": "Brand Search"},
        {**snap["campaigns"][0], "campaign_id": "2", "name": "Brand PMax"},
    ]

    async def fake_for_user(_user_id):
        return _FakeAdsClient()

    async def fake_snapshot(**_kwargs):
        return snap

    monkeypatch.setattr("app.agent.tools.google_ads.GoogleAdsClient.for_user", fake_for_user)
    monkeypatch.setattr("app.agent.tools.google_ads_snapshot.get_or_build_snapshot", fake_snapshot)

    out = await _get_google_ads_data(
        {
            "customer_id": "123",
            "scope": "account_overview",
            "campaign_name": "Brand",
            "include_charts": True,
            "chart_metrics": ["conv_rate"],
        },
        user_id=1,
        channel_id="C",
        thread_ts="T",
    )

    assert out["needs_disambiguation"] is True
    assert "blocks" not in out
