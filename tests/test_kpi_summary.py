"""Tests for the hardened /kpi explanation fact pack + reconciliation guard."""

from app.llm.kpi_summary import (
    _collect_numbers,
    _compact_fact_pack,
    _comparison_facts,
    _note_reconciles,
)


def _comp(cur, prev, change_pct):
    return {"current": cur, "previous": prev, "change_pct": change_pct}


def test_comparison_facts_precomputes_derived_metrics_and_changes():
    facts = _comparison_facts(
        _comp(
            {"cost": 100.0, "conversions": 4.0, "clicks": 50, "impressions": 1000,
             "metric_value": 25.0},
            {"cost": 80.0, "conversions": 8.0, "clicks": 40, "impressions": 800,
             "metric_value": 10.0},
            150.0,
        )
    )
    # CPC is money; conv_rate / ctr are percentages.
    assert facts["current"]["cpc"] == 2.0  # 100 / 50
    assert facts["current"]["conv_rate"] == 8.0  # 4 / 50 * 100
    assert facts["current"]["ctr"] == 5.0  # 50 / 1000 * 100
    # Every delta is precomputed so the model never divides.
    assert facts["change"]["cost_pct"] == 25.0  # (100 - 80) / 80
    assert facts["change"]["conversions_pct"] == -50.0
    assert facts["change"]["metric_pct"] == 150.0  # headline metric, from upstream


def test_comparison_facts_none_for_missing_comparison():
    assert _comparison_facts(None) is None


def test_compact_fact_pack_caps_campaigns_and_keys_by_id():
    report = {
        "mode": "cpa",
        "account": {"name": "Jump.ca"},
        "overall": {"mom": None},
        "campaigns": [
            {
                "campaign_id": f"c{i}",
                "name": f"Campaign {i}",
                "channel": "SEARCH",
                "wow": None,
                "mom": _comp(
                    {"cost": 10.0, "conversions": 1.0, "clicks": 5,
                     "impressions": 100, "metric_value": 10.0},
                    {"cost": 20.0, "conversions": 1.0, "clicks": 5,
                     "impressions": 100, "metric_value": 20.0},
                    -50.0,
                ),
                "drivers": [],
            }
            for i in range(15)
        ],
    }
    pack = _compact_fact_pack(report)
    assert len(pack["campaigns"]) == 10  # capped at top 10
    assert pack["campaigns"][0]["id"] == "c0"
    assert pack["campaigns"][0]["mom"]["change"]["metric_pct"] == -50.0


def test_note_reconciles_accepts_quoted_facts():
    allowed = _collect_numbers({"a": 64.88, "b": 39.11, "pct": -34.09})
    assert _note_reconciles(
        "MOM cost/conv. improved 34.09% ($39.11 vs $64.88).", allowed
    )


def test_note_reconciles_rejects_invented_percentage():
    allowed = _collect_numbers({"a": 64.88, "b": 39.11})
    # "21%" is nowhere in the facts — the note must be rejected.
    assert not _note_reconciles("conversions rose 21% to $39.11.", allowed)


def test_note_reconciles_rejects_invented_money():
    allowed = _collect_numbers({"cost": 41.01})
    assert not _note_reconciles("keyword cost fell to $9.99.", allowed)


def test_note_reconciles_allows_rounding_on_percentages():
    allowed = _collect_numbers({"pct": -58.46})
    assert _note_reconciles("CPA improved 58.5%.", allowed)
    assert _note_reconciles("CPA improved 58%.", allowed)


def test_note_reconciles_skips_bare_integers():
    allowed = _collect_numbers({"x": 12.5})
    # "1" and "4" are bare conversion counts — not checked.
    assert _note_reconciles("conversions rose from 1 to 4.", allowed)


def test_note_reconciles_handles_thousands_separator():
    allowed = _collect_numbers({"cost": 1234.56})
    assert _note_reconciles("spend reached $1,234.56.", allowed)
