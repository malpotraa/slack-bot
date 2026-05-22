"""Channel-aware Google Ads drill-downs (the deeper tier).

The surface snapshot (`google_ads_snapshot`) answers account/campaign-level
questions. When the user digs into one campaign, this module pulls the
channel-appropriate breakdown — reportable resources differ sharply by campaign
type:

  Search       → ad groups, keywords, search terms, ads
  Shopping     → ad groups, products, ads
  Performance Max → asset groups, products  (no ad groups / keywords)
  Demand Gen / Video / Display → ad groups, ads

All metrics are computed deterministically here; the agent only narrates.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from app.integrations.google_ads import DateWindow, GoogleAdsAccount, GoogleAdsClient
from app.integrations.google_ads_snapshot import compute_analysis_windows, derive_metrics
from app.utils.cache import KeyedLocks, TTLCache

_DRILL_CACHE: TTLCache[tuple, dict[str, Any]] = TTLCache(maxsize=128, ttl_seconds=20 * 60)
_DRILL_LOCKS = KeyedLocks(maxsize=128)

# ── Drill dimensions ───────────────────────────────────────────────────────

AD_GROUPS = "ad_groups"
KEYWORDS = "keywords"
SEARCH_TERMS = "search_terms"
ADS = "ads"
ASSET_GROUPS = "asset_groups"
PRODUCTS = "products"

# What `campaign_detail` pulls per advertising channel type.
CHANNEL_DETAIL_DIMENSIONS: dict[str, list[str]] = {
    "SEARCH": [AD_GROUPS, KEYWORDS],
    "SHOPPING": [AD_GROUPS, PRODUCTS],
    "PERFORMANCE_MAX": [ASSET_GROUPS, PRODUCTS],
    "DEMAND_GEN": [AD_GROUPS, ADS],
    "VIDEO": [AD_GROUPS, ADS],
    "DISPLAY": [AD_GROUPS, ADS],
    "MULTI_CHANNEL": [AD_GROUPS, ADS],  # App campaigns
}
_DEFAULT_DETAIL = [AD_GROUPS]

# Dimensions that actually exist for a given channel — used to redirect
# impossible requests (e.g. keywords on Performance Max) to something useful.
CHANNEL_VALID_DIMENSIONS: dict[str, set[str]] = {
    "SEARCH": {AD_GROUPS, KEYWORDS, SEARCH_TERMS, ADS},
    "SHOPPING": {AD_GROUPS, PRODUCTS, ADS},
    "PERFORMANCE_MAX": {ASSET_GROUPS, PRODUCTS},
    "DEMAND_GEN": {AD_GROUPS, ADS},
    "VIDEO": {AD_GROUPS, ADS},
    "DISPLAY": {AD_GROUPS, ADS},
    "MULTI_CHANNEL": {AD_GROUPS, ADS},
}

_ROW_LIMIT = 50


# ── Metric extraction ──────────────────────────────────────────────────────


def _units(micros: Any) -> float:
    try:
        return float(micros or 0) / 1_000_000
    except (TypeError, ValueError):
        return 0.0


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _metrics(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "cost": _units(m.get("costMicros")),
        "conversions": _num(m.get("conversions")),
        "conversion_value": _num(m.get("conversionsValue")),
        "clicks": int(_num(m.get("clicks"))),
        "impressions": int(_num(m.get("impressions"))),
    }


def _finalize(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive ratios for each row, sort by cost, cap the list."""
    out: list[dict[str, Any]] = []
    for r in raw:
        derived = derive_metrics(r)
        for key in ("label", "sublabel", "status", "match_type", "ad_type"):
            if r.get(key) is not None:
                derived[key] = r[key]
        out.append(derived)
    out.sort(key=lambda x: -(x.get("cost") or 0))
    return out[:_ROW_LIMIT]


def _date_clause(window: DateWindow) -> str:
    return (
        f"segments.date BETWEEN '{window.start.isoformat()}' "
        f"AND '{window.end.isoformat()}'"
    )


# ── Per-dimension fetchers ─────────────────────────────────────────────────


async def _fetch_ad_groups(
    client: GoogleAdsClient, customer_id: str, campaign_id: str, window: DateWindow
) -> list[dict[str, Any]]:
    query = f"""
        SELECT ad_group.id, ad_group.name, ad_group.status,
          metrics.cost_micros, metrics.conversions, metrics.conversions_value,
          metrics.clicks, metrics.impressions
        FROM ad_group
        WHERE campaign.id = {campaign_id} AND {_date_clause(window)}
    """
    rows = await client.search_stream(customer_id, query)
    raw = []
    for r in rows:
        ag = r.get("adGroup") or {}
        raw.append(
            {
                "label": str(ag.get("name") or ag.get("id") or "(unnamed)"),
                "status": str(ag.get("status") or ""),
                **_metrics(r.get("metrics") or {}),
            }
        )
    return _finalize(raw)


async def _fetch_keywords(
    client: GoogleAdsClient, customer_id: str, campaign_id: str, window: DateWindow
) -> list[dict[str, Any]]:
    query = f"""
        SELECT ad_group.name, ad_group_criterion.keyword.text,
          ad_group_criterion.keyword.match_type, ad_group_criterion.status,
          metrics.cost_micros, metrics.conversions, metrics.conversions_value,
          metrics.clicks, metrics.impressions
        FROM keyword_view
        WHERE campaign.id = {campaign_id} AND {_date_clause(window)}
        ORDER BY metrics.cost_micros DESC
        LIMIT {_ROW_LIMIT}
    """
    rows = await client.search_stream(customer_id, query)
    raw = []
    for r in rows:
        crit = (r.get("adGroupCriterion") or {}).get("keyword") or {}
        raw.append(
            {
                "label": str(crit.get("text") or "(unknown)"),
                "match_type": str(crit.get("matchType") or ""),
                "sublabel": str((r.get("adGroup") or {}).get("name") or ""),
                "status": str((r.get("adGroupCriterion") or {}).get("status") or ""),
                **_metrics(r.get("metrics") or {}),
            }
        )
    return _finalize(raw)


async def _fetch_search_terms(
    client: GoogleAdsClient, customer_id: str, campaign_id: str, window: DateWindow
) -> list[dict[str, Any]]:
    query = f"""
        SELECT search_term_view.search_term, search_term_view.status,
          metrics.cost_micros, metrics.conversions, metrics.conversions_value,
          metrics.clicks, metrics.impressions
        FROM search_term_view
        WHERE campaign.id = {campaign_id} AND {_date_clause(window)}
        ORDER BY metrics.cost_micros DESC
        LIMIT {_ROW_LIMIT}
    """
    rows = await client.search_stream(customer_id, query)
    raw = []
    for r in rows:
        stv = r.get("searchTermView") or {}
        raw.append(
            {
                "label": str(stv.get("searchTerm") or "(unknown)"),
                "status": str(stv.get("status") or ""),
                **_metrics(r.get("metrics") or {}),
            }
        )
    return _finalize(raw)


async def _fetch_ads(
    client: GoogleAdsClient, customer_id: str, campaign_id: str, window: DateWindow
) -> list[dict[str, Any]]:
    query = f"""
        SELECT ad_group.name, ad_group_ad.ad.id, ad_group_ad.ad.name,
          ad_group_ad.ad.type, ad_group_ad.status,
          metrics.cost_micros, metrics.conversions, metrics.conversions_value,
          metrics.clicks, metrics.impressions
        FROM ad_group_ad
        WHERE campaign.id = {campaign_id} AND {_date_clause(window)}
        ORDER BY metrics.cost_micros DESC
        LIMIT {_ROW_LIMIT}
    """
    rows = await client.search_stream(customer_id, query)
    raw = []
    for r in rows:
        ad = (r.get("adGroupAd") or {}).get("ad") or {}
        ad_type = str(ad.get("type") or "").replace("_", " ").title()
        label = str(ad.get("name") or "") or f"{ad_type or 'Ad'} {ad.get('id') or ''}".strip()
        raw.append(
            {
                "label": label or "(ad)",
                "ad_type": ad_type,
                "sublabel": str((r.get("adGroup") or {}).get("name") or ""),
                "status": str((r.get("adGroupAd") or {}).get("status") or ""),
                **_metrics(r.get("metrics") or {}),
            }
        )
    return _finalize(raw)


async def _fetch_asset_groups(
    client: GoogleAdsClient, customer_id: str, campaign_id: str, window: DateWindow
) -> list[dict[str, Any]]:
    query = f"""
        SELECT asset_group.id, asset_group.name, asset_group.status,
          asset_group.ad_strength,
          metrics.cost_micros, metrics.conversions, metrics.conversions_value,
          metrics.clicks, metrics.impressions
        FROM asset_group
        WHERE campaign.id = {campaign_id} AND {_date_clause(window)}
    """
    rows = await client.search_stream(customer_id, query)
    raw = []
    for r in rows:
        ag = r.get("assetGroup") or {}
        raw.append(
            {
                "label": str(ag.get("name") or ag.get("id") or "(unnamed)"),
                "sublabel": str(ag.get("adStrength") or "").replace("_", " ").title(),
                "status": str(ag.get("status") or ""),
                **_metrics(r.get("metrics") or {}),
            }
        )
    return _finalize(raw)


async def _fetch_products(
    client: GoogleAdsClient, customer_id: str, campaign_id: str, window: DateWindow
) -> list[dict[str, Any]]:
    query = f"""
          SELECT segments.product_item_id, segments.product_title,
          metrics.cost_micros, metrics.conversions, metrics.conversions_value,
          metrics.clicks, metrics.impressions
        FROM shopping_performance_view
        WHERE campaign.id = {campaign_id} AND {_date_clause(window)}
        ORDER BY metrics.cost_micros DESC
        LIMIT {_ROW_LIMIT}
    """
    rows = await client.search_stream(customer_id, query)
    # shopping_performance_view rows segment by product; aggregate per item.
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        seg = r.get("segments") or {}
        item_id = str(seg.get("productItemId") or "(unknown)")
        bucket = agg.setdefault(
            item_id,
            {
                "label": str(seg.get("productTitle") or item_id),
                "sublabel": item_id,
                "cost": 0.0,
                "conversions": 0.0,
                "conversion_value": 0.0,
                "clicks": 0,
                "impressions": 0,
            },
        )
        m = _metrics(r.get("metrics") or {})
        for k in (
            "cost",
            "conversions",
            "conversion_value",
            "clicks",
            "impressions",
        ):
            bucket[k] += m[k]
    return _finalize(list(agg.values()))


_FETCHERS = {
    AD_GROUPS: _fetch_ad_groups,
    KEYWORDS: _fetch_keywords,
    SEARCH_TERMS: _fetch_search_terms,
    ADS: _fetch_ads,
    ASSET_GROUPS: _fetch_asset_groups,
    PRODUCTS: _fetch_products,
}


# ── Campaign resolution + drill orchestration ──────────────────────────────


def resolve_campaign(
    snapshot: dict[str, Any],
    *,
    campaign_id: str | None = None,
    campaign_name: str | None = None,
) -> dict[str, Any] | list[dict[str, Any]] | None:
    """Find a campaign in the surface snapshot. Returns the campaign row, a
    list of candidates when a name is ambiguous, or None when nothing matches.
    """
    campaigns = snapshot.get("campaigns") or []
    if campaign_id:
        cid = str(campaign_id).strip()
        for c in campaigns:
            if str(c.get("campaign_id")) == cid:
                return c
        return None
    if campaign_name:
        needle = " ".join(campaign_name.split()).casefold()
        exact = [c for c in campaigns if (c.get("name") or "").casefold() == needle]
        if exact:
            return exact[0]
        partial = [c for c in campaigns if needle in (c.get("name") or "").casefold()]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            return partial
        return None
    return None


def dimensions_for(channel: str, scope: str) -> tuple[list[str], str | None]:
    """Resolve the drill scope to concrete dimensions for this channel type.

    Returns (dimensions, note). `note` is set when the requested scope had to
    be redirected because it doesn't exist for the campaign type.
    """
    channel = (channel or "").upper()
    valid = CHANNEL_VALID_DIMENSIONS.get(channel, {AD_GROUPS, ADS})

    if scope in ("campaign_detail", "", None):
        return CHANNEL_DETAIL_DIMENSIONS.get(channel, _DEFAULT_DETAIL), None

    requested = {
        "keywords": KEYWORDS,
        "search_terms": SEARCH_TERMS,
        "ad_groups": AD_GROUPS,
        "ads": ADS,
        "asset_groups": ASSET_GROUPS,
        "products": PRODUCTS,
    }.get(scope)
    if requested is None:
        return CHANNEL_DETAIL_DIMENSIONS.get(channel, _DEFAULT_DETAIL), None
    if requested in valid:
        return [requested], None
    # Requested dimension doesn't exist for this channel — redirect.
    fallback = CHANNEL_DETAIL_DIMENSIONS.get(channel, _DEFAULT_DETAIL)
    note = (
        f"{channel.replace('_', ' ').title()} campaigns have no "
        f"{scope.replace('_', ' ')} — showing {', '.join(fallback)} instead."
    )
    return fallback, note


async def build_drill_pack(
    *,
    client: GoogleAdsClient,
    account: GoogleAdsAccount,
    campaign: dict[str, Any],
    dimensions: list[str],
) -> dict[str, Any]:
    """Fetch the requested drill dimensions for one campaign and assemble a
    fully-computed fact pack. The `campaign` block comes from the surface
    snapshot (it already carries last-30/prior-30 metrics and deltas).
    """
    window = compute_analysis_windows(account.time_zone).last_30
    campaign_id = str(campaign.get("campaign_id"))

    results = await asyncio.gather(
        *[_FETCHERS[d](client, account.customer_id, campaign_id, window) for d in dimensions],
        return_exceptions=True,
    )
    breakdown: dict[str, Any] = {}
    for dim, res in zip(dimensions, results, strict=True):
        if isinstance(res, BaseException):
            logger.warning(f"Google Ads drill '{dim}' failed: {res}")
            breakdown[dim] = {"error": str(res), "rows": []}
        else:
            breakdown[dim] = {"rows": res}

    return {
        "account": {
            "name": account.name,
            "customer_id": account.customer_id,
            "currency": account.currency_code,
        },
        "window": window.as_dict(),
        "campaign": {
            "campaign_id": campaign_id,
            "name": campaign.get("name"),
            "channel": campaign.get("channel"),
            "status": campaign.get("status"),
            "last_30": campaign.get("last_30"),
            "prior_30": campaign.get("prior_30"),
            "delta_30d_vs_prior_30": campaign.get("delta_30d_vs_prior_30"),
            "impression_share": campaign.get("impression_share"),
            "budget": campaign.get("budget"),
        },
        "breakdown": breakdown,
        "notes": [
            "Drill metrics cover the last 30 days (excluding today).",
            "Rows are sorted by cost; long lists are capped.",
        ],
    }


async def get_or_build_drill(
    *,
    client: GoogleAdsClient,
    account: GoogleAdsAccount,
    campaign: dict[str, Any],
    dimensions: list[str],
    cache_key: tuple,
) -> dict[str, Any]:
    """Return a cached drill pack for this thread/campaign/dimensions, or build
    one. A per-key lock means concurrent misses share a single pull."""
    cached = _DRILL_CACHE.get(cache_key)
    if cached is not None:
        return cached
    async with _DRILL_LOCKS.get(cache_key):
        cached = _DRILL_CACHE.get(cache_key)  # re-check inside the lock
        if cached is not None:
            return cached
        pack = await build_drill_pack(
            client=client, account=account, campaign=campaign, dimensions=dimensions
        )
        _DRILL_CACHE.set(cache_key, pack)
        return pack
