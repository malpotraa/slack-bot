"""Block Kit cards + value formatting for Google Ads answers."""

from app.formatters.ads_blocks import (
    account_overview_card,
    campaign_detail_card,
    channel_label,
    count,
    delta,
    money,
    pct,
    ratio,
)
from app.integrations.google_ads_snapshot import derive_metrics


def _m(cost, conv, value, clicks=2000, impr=40000):
    return derive_metrics(
        {
            "cost": cost,
            "conversions": conv,
            "conversion_value": value,
            "clicks": clicks,
            "impressions": impr,
        }
    )


def _section_text(blocks):
    return "\n".join(
        b["text"]["text"] for b in blocks if b.get("type") == "section"
    )


# ── Value formatters ───────────────────────────────────────────────────────


def test_value_formatters():
    assert money(1234, "USD") == "$1,234"
    assert money(12_000, "USD") == "$12K"
    assert money(None, "USD") == "—"
    assert count(182) == "182"
    assert ratio(4.2) == "4.20x"
    assert pct(0.0734) == "7.3%"
    assert pct(None) == "—"
    assert delta(33.0) == "+33%"
    assert delta(-12.0) == "-12%"
    assert delta(None) == "—"
    assert channel_label("PERFORMANCE_MAX") == "PMax"


# ── Account overview card ──────────────────────────────────────────────────


def _snapshot():
    return {
        "account": {"name": "Acme", "customer_id": "1234567890", "currency": "USD"},
        "as_of": "2026-05-19",
        "windows": {
            "last_30": {
                "label": "last_30",
                "start": "2026-04-20",
                "end": "2026-05-19",
            }
        },
        "by_channel": {
            "SEARCH": {
                "campaign_count": 1,
                "last_30": _m(4000, 180, 24000),
                "prior_30": _m(3000, 150, 18000),
                "delta_30d_vs_prior_30": {},
            }
        },
        "campaigns": [
            {
                "campaign_id": "1",
                "name": "Brand Search",
                "channel": "SEARCH",
                "last_30": _m(4000, 180, 24000),
                "delta_30d_vs_prior_30": {
                    "cost_pct": 33.0,
                    "conversions_pct": 20.0,
                    "cpa_pct": 10.0,
                    "roas_pct": -5.0,
                },
            }
        ],
    }


def test_overview_defaults_to_cost_per_conv_with_new_columns():
    text = _section_text(account_overview_card(_snapshot()))
    assert "Cost/conv" in text
    assert "ROAS" not in text  # ROAS never shows by default
    assert "CPC" in text and "Cv rate" in text and "CTR" in text


def test_overview_header_shows_date_range():
    header = account_overview_card(_snapshot())[0]["text"]["text"]
    assert "Apr 20" in header and "May 19, 2026" in header


def test_overview_roas_mode_swaps_the_metric_column():
    text = _section_text(account_overview_card(_snapshot(), metric="roas"))
    assert "ROAS" in text
    assert "Cost/conv" not in text


def test_overview_change_mode_adds_delta_columns():
    text = _section_text(account_overview_card(_snapshot(), include_change=True))
    assert "Cost Δ" in text
    assert "+33%" in text  # the precomputed cost delta is rendered


def test_overview_has_drill_buttons():
    actions = [
        b for b in account_overview_card(_snapshot()) if b.get("type") == "actions"
    ]
    assert actions and actions[0]["elements"][0]["action_id"].startswith(
        "ads_drill_campaign:"
    )


# ── Campaign drill-down card ───────────────────────────────────────────────


def test_campaign_detail_uses_cost_per_conv_columns():
    drill = {
        "account": {"name": "Acme", "currency": "USD"},
        "window": {"start": "2026-04-20", "end": "2026-05-19"},
        "campaign": {"campaign_id": "1", "name": "Brand Search", "channel": "SEARCH"},
        "breakdown": {
            "keywords": {
                "rows": [dict(_m(800, 40, 4000), label="running shoes")]
            },
            "ad_groups": {"error": "boom", "rows": []},
        },
    }
    blocks = campaign_detail_card(drill)
    text = _section_text(blocks)
    assert "running shoes" in text
    assert "Cost/conv" in text and "ROAS" not in text
    assert "couldn't load" in text  # failed dimension degrades gracefully
