from app.agent.tools import _fetch_google_ads_kpis
from app.integrations.google_ads import _with_metric


async def test_fetch_google_ads_kpis_passes_explanations_to_report_blocks(monkeypatch):
    report = {
        "account": {"name": "Jump.ca"},
        "mode": "cpa",
        "windows": {},
        "campaigns": [],
        "overall": {},
    }
    seen = {}

    async def fake_build_kpi_report(*, user_id, customer_id, mode, frozen_windows):
        seen["build"] = {
            "user_id": user_id,
            "customer_id": customer_id,
            "mode": mode,
            "frozen_windows": frozen_windows,
        }
        return report

    def fake_report_blocks(got_report, explanations=None):
        seen["report"] = got_report
        seen["explanations"] = explanations
        return [{"type": "section", "text": {"type": "mrkdwn", "text": "report"}}]

    monkeypatch.setattr(
        "app.agent.tools.google_ads.build_kpi_report", fake_build_kpi_report
    )
    monkeypatch.setattr("app.agent.tools.kpi_blocks.report_blocks", fake_report_blocks)

    out = await _fetch_google_ads_kpis(
        {
            "confirmed": True,
            "customer_id": "123-456-7890",
            "mode": "cpa",
            "windows": {"anchor_date": "2026-05-20"},
        },
        user_id=42,
    )

    assert out["ok"] is True
    assert seen["build"] == {
        "user_id": 42,
        "customer_id": "1234567890",
        "mode": "cpa",
        "frozen_windows": {"anchor_date": "2026-05-20"},
    }
    assert seen["report"] is report
    # An empty report has no campaigns/overall, so explain_kpi_report
    # short-circuits to {} without an API call — the deterministic rows render.
    assert seen["explanations"] == {}


def test_kpi_metrics_conversion_rate_uses_conversions_over_clicks():
    row = {
        "cost": 100.0,
        "conversions": 2.0,
        "conversion_value": 0.0,
        "clicks": 10,
    }

    out = _with_metric(row, "cpa")

    assert out["conv_rate"] == 0.2
    assert out["metric_value"] == 50.0
