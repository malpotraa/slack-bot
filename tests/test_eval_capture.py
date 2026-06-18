"""Braintrust eval-capture payload filters."""

from app.evals.capture import capture_ads_turn, extract_deltas, extract_trends


def test_extract_deltas_keeps_only_pct_values():
    out = extract_deltas(
        {
            "account": {"name": "Acme"},
            "account_deltas": {"mom_30d": {"cost_pct": 12.3, "cost": 100}},
        }
    )
    assert out == {"account_deltas": {"mom_30d": {"cost_pct": 12.3}}}


def test_extract_trends_keeps_rate_diagnostics_without_names():
    out = extract_trends(
        {
            "account": {"name": "Acme"},
            "account_trend": {
                "campaign_name": "Brand",
                "metric": "conv_rate",
                "days": 90,
                "earlier_period_value": 0.12,
                "recent_period_value": 0.10,
                "period_value_basis": "weighted_by_clicks",
                "recent_vs_earlier_pct": -16.7,
            },
        }
    )
    assert out == {
        "account_trend": {
            "metric": "conv_rate",
            "days": 90,
            "earlier_period_value": 0.12,
            "recent_period_value": 0.10,
            "period_value_basis": "weighted_by_clicks",
            "recent_vs_earlier_pct": -16.7,
        }
    }


def test_capture_ads_turn_records_tool_name(monkeypatch):
    monkeypatch.setattr("app.evals.capture.settings.eval_capture", True)
    monkeypatch.setattr("app.evals.capture.settings.braintrust_api_key", "key")

    inserted = {}

    class _Dataset:
        def insert(self, **kwargs):
            inserted.update(kwargs)

        def flush(self):
            pass

    class _Braintrust:
        @staticmethod
        def init_dataset(**_kwargs):
            return _Dataset()

    import sys

    monkeypatch.setitem(sys.modules, "braintrust", _Braintrust)

    capture_ads_turn(
        user_message="how is account doing",
        reply="Last 30 days...",
        fact_pack={},
        tool_name="actual_tool_name",
        tool_args={"scope": "account_overview"},
        prompt_version="test",
        cost_usd=0.01,
        thread_ts="123",
    )

    assert inserted["input"]["tool_args"]["tool_name"] == "actual_tool_name"
