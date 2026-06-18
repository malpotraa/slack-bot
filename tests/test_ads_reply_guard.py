"""Runtime guard for unsupported percentage claims in Ads replies."""

from app.agent.runner import _ads_reply_unverified_percentages


def test_ads_reply_guard_allows_percentages_from_deltas_and_rates():
    fact_pack = {
        "account_deltas": {"mom_30d": {"conv_rate_pct": -10.2}},
        "account_totals": {"last_30": {"conv_rate": 0.1261}},
        "account_trend": {
            "metric": "conv_rate",
            "earlier_period_value": 0.1332,
            "recent_period_value": 0.1143,
            "recent_vs_earlier_pct": -14.2,
        },
    }

    reply = "Conv rate was 12.61%, down -10.2%; 13.32% vs 11.43% is -14.2%."

    assert _ads_reply_unverified_percentages(reply, fact_pack) == []


def test_ads_reply_guard_blocks_percentages_not_in_current_fact_pack():
    fact_pack = {
        "account_totals": {"last_30": {"conv_rate": 0.1261}},
    }

    reply = "Earlier avg was 13.32% and recent avg was 11.43%."

    assert _ads_reply_unverified_percentages(reply, fact_pack) == [13.32, 11.43]
