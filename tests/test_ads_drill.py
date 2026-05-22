"""Campaign resolution + channel-aware drill dimension routing."""

from app.integrations.google_ads_drill import (
    AD_GROUPS,
    ADS,
    ASSET_GROUPS,
    KEYWORDS,
    PRODUCTS,
    dimensions_for,
    resolve_campaign,
)


def _snapshot():
    return {
        "campaigns": [
            {"campaign_id": "1", "name": "Brand Search", "channel": "SEARCH"},
            {"campaign_id": "2", "name": "PMax Retail", "channel": "PERFORMANCE_MAX"},
            {"campaign_id": "3", "name": "Brand Display", "channel": "DISPLAY"},
        ]
    }


# ── resolve_campaign ───────────────────────────────────────────────────────


def test_resolve_campaign_by_id():
    assert resolve_campaign(_snapshot(), campaign_id="2")["name"] == "PMax Retail"


def test_resolve_campaign_by_exact_name():
    assert resolve_campaign(_snapshot(), campaign_name="pmax retail")["campaign_id"] == "2"


def test_resolve_campaign_ambiguous_name_returns_candidates():
    res = resolve_campaign(_snapshot(), campaign_name="brand")
    assert isinstance(res, list)
    assert {c["campaign_id"] for c in res} == {"1", "3"}


def test_resolve_campaign_unknown_returns_none():
    assert resolve_campaign(_snapshot(), campaign_name="nonsense") is None
    assert resolve_campaign(_snapshot(), campaign_id="999") is None


# ── dimensions_for ─────────────────────────────────────────────────────────


def test_dimensions_for_search_detail():
    dims, note = dimensions_for("SEARCH", "campaign_detail")
    assert dims == [AD_GROUPS, KEYWORDS]
    assert note is None


def test_dimensions_for_specific_scope():
    dims, note = dimensions_for("SEARCH", "keywords")
    assert dims == [KEYWORDS]
    assert note is None


def test_dimensions_for_pmax_detail():
    dims, _ = dimensions_for("PERFORMANCE_MAX", "campaign_detail")
    assert dims == [ASSET_GROUPS, PRODUCTS]


def test_dimensions_for_pmax_keywords_redirects():
    # Performance Max has no keywords — must redirect, with an explaining note.
    dims, note = dimensions_for("PERFORMANCE_MAX", "keywords")
    assert KEYWORDS not in dims
    assert dims == [ASSET_GROUPS, PRODUCTS]
    assert note and "keyword" in note.lower()


def test_dimensions_for_display_ads_scope():
    dims, note = dimensions_for("DISPLAY", "ads")
    assert dims == [ADS]
    assert note is None
