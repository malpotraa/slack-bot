"""Block Kit cards for Google Ads answers — monospace tables + drill buttons.

The agent narrates the insight as streamed text; these cards carry the numbers.
Tables default to cost/conv (CPA) plus CPC, conversion rate and CTR — ROAS only
when the caller passes metric="roas", percent-change columns only when
include_change is set. Charts are uploaded separately and only on request.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from app.utils.slack_mrkdwn import escape_slack_text

ADS_DRILL_ACTION = "ads_drill_campaign"

_MAX_TABLE_ROWS = 12
_MAX_DRILL_BUTTONS = 5
_NAME_WIDTH = 22

_CHANNEL_LABELS = {
    "SEARCH": "Search",
    "PERFORMANCE_MAX": "PMax",
    "SHOPPING": "Shopping",
    "DEMAND_GEN": "Demand Gen",
    "VIDEO": "Video",
    "DISPLAY": "Display",
    "MULTI_CHANNEL": "App",
}
_CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "$", "AUD": "$"}

_DIMENSION_TITLES = {
    "ad_groups": "Ad groups",
    "keywords": "Top keywords",
    "search_terms": "Top search terms",
    "ads": "Ads",
    "asset_groups": "Asset groups",
    "products": "Top products",
}
_DIMENSION_ENTITY = {
    "ad_groups": "Ad group",
    "keywords": "Keyword",
    "search_terms": "Search term",
    "ads": "Ad",
    "asset_groups": "Asset group",
    "products": "Product",
}


# ── Value formatting ───────────────────────────────────────────────────────


def channel_label(channel: str | None) -> str:
    return _CHANNEL_LABELS.get((channel or "").upper(), (channel or "—").title())


def currency_symbol(currency: str | None) -> str:
    return _CURRENCY_SYMBOLS.get((currency or "USD").upper(), f"{currency} ")


def money(value: Any, currency: str | None) -> str:
    if value is None:
        return "—"
    v = float(value)
    sym = currency_symbol(currency)
    if abs(v) >= 1_000_000:
        return f"{sym}{v / 1_000_000:.1f}M"
    if abs(v) >= 10_000:
        return f"{sym}{v / 1_000:.0f}K"
    return f"{sym}{v:,.0f}"


def count(value: Any) -> str:
    if value is None:
        return "—"
    v = float(value)
    if abs(v) >= 10_000:
        return f"{v / 1_000:.0f}K"
    if v and v != int(v):
        return f"{v:.1f}"
    return f"{int(v):,}"


def ratio(value: Any) -> str:
    return "—" if value is None else f"{float(value):.2f}x"


def pct(value: Any) -> str:
    """A rate (0–1 fraction) as a percentage — for conversion rate / CTR."""
    return "—" if value is None else f"{float(value) * 100:.1f}%"


def delta(value: Any) -> str:
    """A precomputed percent change as a signed integer percentage."""
    return "—" if value is None else f"{float(value):+.0f}%"


def _clean(text: Any, width: int = _NAME_WIDTH) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= width else s[: width - 1] + "…"


def _format_range(window: dict | None) -> str:
    """Render a {start, end} ISO window as 'Apr 20 – May 19, 2026'."""
    if not window:
        return ""
    try:
        start = date.fromisoformat(window["start"])
        end = date.fromisoformat(window["end"])
    except Exception:
        return ""
    if start.year == end.year and start.month == end.month:
        return f"{start:%b %-d}–{end:%-d, %Y}"
    if start.year == end.year:
        return f"{start:%b %-d} – {end:%b %-d, %Y}"
    return f"{start:%b %-d, %Y} – {end:%b %-d, %Y}"


# ── Monospace table ────────────────────────────────────────────────────────


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """A fixed-width text table in a Slack code block. First column is
    left-aligned, the rest right-aligned."""
    if not rows:
        return "```\n(no rows)\n```"
    cols = len(headers)
    widths = [len(h) for h in headers]
    for row in rows:
        for i in range(cols):
            widths[i] = max(widths[i], len(row[i]))

    def fmt(cells: list[str]) -> str:
        out = [cells[0].ljust(widths[0])]
        out += [cells[i].rjust(widths[i]) for i in range(1, cols)]
        return "  ".join(out)

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines += [fmt(r) for r in rows]
    return "```\n" + "\n".join(lines) + "\n```"


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _metric_header(metric: str) -> str:
    return "ROAS" if metric == "roas" else "Cost/conv"


def _metric_cell(m: dict, metric: str, currency: str | None) -> str:
    return ratio(m.get("roas")) if metric == "roas" else money(m.get("cpa"), currency)


# ── Account overview card ──────────────────────────────────────────────────


def account_overview_card(
    snapshot: dict[str, Any],
    *,
    metric: str = "cost_per_conv",
    include_change: bool = False,
) -> list[dict]:
    account = snapshot.get("account") or {}
    currency = account.get("currency")
    name = escape_slack_text(account.get("name") or "Google Ads account")

    window = (snapshot.get("windows") or {}).get("last_30") or {}
    rng = _format_range(window)
    header = f"📊 *Google Ads — {name}*"
    if rng:
        header += f" · {rng} (last 30 days)"
    blocks: list[dict] = [_section(header)]

    by_channel = snapshot.get("by_channel") or {}
    if by_channel:
        parts = []
        for channel, agg in sorted(
            by_channel.items(), key=lambda kv: -(kv[1]["last_30"]["cost"] or 0)
        ):
            d = agg["last_30"]
            tail = (
                f"{ratio(d['roas'])} ROAS"
                if metric == "roas"
                else f"{money(d['cpa'], currency)} cost/conv"
            )
            parts.append(
                f"• *{channel_label(channel)}* — {money(d['cost'], currency)} "
                f"spend · {count(d['conversions'])} conv · {tail}"
            )
        blocks.append(_section("*By channel*\n" + "\n".join(parts)))

    campaigns = (snapshot.get("campaigns") or [])[:_MAX_TABLE_ROWS]
    if campaigns:
        blocks.append(_section("*Campaigns*"))
        blocks.append(
            _section(_campaign_table(campaigns, currency, metric, include_change))
        )

    buttons = _drill_buttons(account.get("customer_id"), snapshot.get("campaigns") or [])
    if buttons:
        blocks.append({"type": "actions", "block_id": "ads_drill", "elements": buttons})

    footer = "Account & campaign level · read-only · tap a campaign to drill in"
    if snapshot.get("as_of"):
        footer = f"Data through {snapshot['as_of']} · {footer}"
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]})
    return blocks[:48]


def _campaign_table(
    campaigns: list[dict], currency: str | None, metric: str, include_change: bool
) -> str:
    mh = _metric_header(metric)
    if include_change:
        headers = ["Campaign", "Cost", "Cost Δ", "Conv", "Conv Δ", mh, f"{mh} Δ"]
        rows = []
        for c in campaigns:
            m = c.get("last_30") or {}
            d = c.get("delta_30d_vs_prior_30") or {}
            metric_delta = d.get("roas_pct") if metric == "roas" else d.get("cpa_pct")
            rows.append(
                [
                    _clean(c.get("name")),
                    money(m.get("cost"), currency),
                    delta(d.get("cost_pct")),
                    count(m.get("conversions")),
                    delta(d.get("conversions_pct")),
                    _metric_cell(m, metric, currency),
                    delta(metric_delta),
                ]
            )
        return _table(headers, rows)

    headers = ["Campaign", "Cost", "Conv", "CPC", mh, "Cv rate", "CTR"]
    rows = []
    for c in campaigns:
        m = c.get("last_30") or {}
        rows.append(
            [
                _clean(c.get("name")),
                money(m.get("cost"), currency),
                count(m.get("conversions")),
                money(m.get("cpc"), currency),
                _metric_cell(m, metric, currency),
                pct(m.get("conv_rate")),
                pct(m.get("ctr")),
            ]
        )
    return _table(headers, rows)


def _drill_buttons(customer_id: str | None, campaigns: list[dict]) -> list[dict]:
    buttons: list[dict] = []
    for c in campaigns[:_MAX_DRILL_BUTTONS]:
        name = c.get("name") or "campaign"
        value = json.dumps(
            {
                "customer_id": customer_id or "",
                "campaign_id": str(c.get("campaign_id") or ""),
                "campaign_name": name[:120],
            },
            separators=(",", ":"),
        )
        if len(value) > 1990:
            continue
        buttons.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": f"🔍 {_clean(name, 24)}"},
                "action_id": f"{ADS_DRILL_ACTION}:{c.get('campaign_id')}",
                "value": value,
            }
        )
    return buttons


# ── Campaign drill-down card ───────────────────────────────────────────────


def campaign_detail_card(
    drill: dict[str, Any], *, metric: str = "cost_per_conv"
) -> list[dict]:
    account = drill.get("account") or {}
    currency = account.get("currency")
    campaign = drill.get("campaign") or {}
    name = escape_slack_text(campaign.get("name") or "Campaign")

    rng = _format_range(drill.get("window"))
    header = f"🔍 *{name}* — {channel_label(campaign.get('channel'))}"
    if rng:
        header += f" · {rng}"
    blocks: list[dict] = [_section(header)]

    mh = _metric_header(metric)
    for dimension, payload in (drill.get("breakdown") or {}).items():
        title = _DIMENSION_TITLES.get(dimension, dimension.replace("_", " ").title())
        if payload.get("error"):
            blocks.append(_section(f"*{title}* — _couldn't load_"))
            continue
        rows_data = (payload.get("rows") or [])[:_MAX_TABLE_ROWS]
        if not rows_data:
            blocks.append(_section(f"*{title}* — _no data_"))
            continue
        entity = _DIMENSION_ENTITY.get(dimension, "Item")
        headers = [entity, "Cost", "Conv", "CPC", mh, "Cv rate", "CTR"]
        rows = [
            [
                _clean(r.get("label"), 26),
                money(r.get("cost"), currency),
                count(r.get("conversions")),
                money(r.get("cpc"), currency),
                _metric_cell(r, metric, currency),
                pct(r.get("conv_rate")),
                pct(r.get("ctr")),
            ]
            for r in rows_data
        ]
        blocks.append(_section(f"*{title}*"))
        blocks.append(_section(_table(headers, rows)))

    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Campaign drill-down · read-only · ask a follow-up to go deeper",
                }
            ],
        }
    )
    return blocks[:48]
