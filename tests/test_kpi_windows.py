"""Deterministic KPI date-window math + pure metric helpers (google_ads)."""

from datetime import date

from app.integrations.google_ads import (
    _metric_value,
    _micros_to_units,
    _pct_change,
    compute_windows,
    normalize_customer_id,
)


def test_compute_windows_midmonth():
    w = compute_windows("UTC", today=date(2026, 5, 20))
    assert w.anchor_date == date(2026, 5, 19)
    assert (w.last_7.start, w.last_7.end) == (date(2026, 5, 13), date(2026, 5, 19))
    assert (w.previous_7.start, w.previous_7.end) == (date(2026, 5, 6), date(2026, 5, 12))
    assert w.mtd is not None
    assert (w.mtd.start, w.mtd.end) == (date(2026, 5, 1), date(2026, 5, 19))
    assert w.previous_mtd is not None
    assert (w.previous_mtd.start, w.previous_mtd.end) == (date(2026, 4, 1), date(2026, 4, 19))


def test_compute_windows_first_of_month_has_no_mtd():
    # yesterday lands in the previous month → MTD/previous-MTD unavailable.
    w = compute_windows("UTC", today=date(2026, 5, 1))
    assert w.anchor_date == date(2026, 4, 30)
    assert w.mtd is None
    assert w.previous_mtd is None


def test_compute_windows_clamps_short_previous_month():
    # Mar 31 → yesterday Mar 30; Feb 2026 has only 28 days, so previous-MTD clamps.
    w = compute_windows("UTC", today=date(2026, 3, 31))
    assert (w.mtd.start, w.mtd.end) == (date(2026, 3, 1), date(2026, 3, 30))
    assert (w.previous_mtd.start, w.previous_mtd.end) == (date(2026, 2, 1), date(2026, 2, 28))


def test_compute_windows_crosses_year_boundary():
    w = compute_windows("UTC", today=date(2026, 1, 15))
    assert (w.previous_mtd.start, w.previous_mtd.end) == (date(2025, 12, 1), date(2025, 12, 14))


def test_compute_windows_unknown_timezone_does_not_raise():
    w = compute_windows("Not/AZone", today=date(2026, 5, 20))
    assert w.anchor_date == date(2026, 5, 19)


def test_normalize_customer_id_strips_non_digits():
    assert normalize_customer_id("123-456-7890") == "1234567890"
    assert normalize_customer_id(1234567890) == "1234567890"
    assert normalize_customer_id(None) == ""


def test_micros_to_units():
    assert _micros_to_units(1_500_000) == 1.5
    assert _micros_to_units(None) == 0.0


def test_metric_value_cpa_and_roas():
    row = {"cost": 100.0, "conversions": 4.0, "conversion_value": 250.0}
    assert _metric_value(row, "cpa") == 25.0
    assert _metric_value(row, "roas") == 2.5


def test_metric_value_guards_division_by_zero():
    assert _metric_value({"cost": 100.0, "conversions": 0.0}, "cpa") is None
    assert _metric_value({"cost": 0.0, "conversion_value": 50.0}, "roas") is None


def test_pct_change():
    assert _pct_change(110.0, 100.0) == 10.0
    assert _pct_change(50.0, 0.0) is None
    assert _pct_change(None, 100.0) is None
