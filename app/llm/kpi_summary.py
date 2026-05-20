"""Agent-written Google Ads KPI explanations from deterministic fact packs."""

from __future__ import annotations

import json
import re
from typing import Any

from anthropic import AsyncAnthropic
from loguru import logger

from app.config import settings

_client_singleton: AsyncAnthropic | None = None


def _client() -> AsyncAnthropic:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _client_singleton


async def explain_kpi_report(report: dict[str, Any]) -> dict[str, str]:
    """Return short explanations keyed by campaign id plus `overall`.

    The model gets only precomputed metrics and driver facts. It must not do
    spend arithmetic or invent causes. If parsing fails, callers can fall back
    to deterministic formatting without explanations.
    """
    fact_pack = _compact_fact_pack(report)
    if not fact_pack["campaigns"] and not fact_pack.get("overall"):
        return {}

    prompt = (
        "You write concise Google Ads performance explanations for Slack.\n"
        "Rules:\n"
        "- Use only the facts in the JSON. Do not invent causes, keywords, or numbers.\n"
        "- Do not recalculate KPI values. The JSON values are authoritative.\n"
        "- Prefer 'visible driver' or 'correlated with' over causal certainty.\n"
        "- If no clear driver is present, return an empty string for that item.\n"
        "- CPA lower is better. ROAS higher is better.\n"
        "- Keep each explanation one sentence, <= 180 characters when possible.\n"
        "Return JSON only: {\"overall\":\"...\", \"campaigns\":{\"<id>\":\"...\"}}.\n\n"
        f"FACTS:\n{json.dumps(fact_pack, separators=(',', ':'))}"
    )
    try:
        resp = await _client().messages.create(
            model=settings.extraction_model,
            max_tokens=900,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
        payload = _parse_json_object(text)
    except Exception as exc:
        logger.warning(f"KPI explanation failed: {exc}")
        return {}

    out: dict[str, str] = {}
    overall = str(payload.get("overall") or "").strip()
    if overall:
        out["overall"] = overall[:300]
    campaigns = payload.get("campaigns") or {}
    if isinstance(campaigns, dict):
        for campaign_id, summary in campaigns.items():
            text = str(summary or "").strip()
            if text:
                out[str(campaign_id)] = text[:300]
    return out


def _compact_fact_pack(report: dict[str, Any]) -> dict[str, Any]:
    mode = report.get("mode") or "cpa"
    campaigns = []
    for row in (report.get("campaigns") or [])[:10]:
        campaigns.append(
            {
                "id": row.get("campaign_id"),
                "name": row.get("name"),
                "channel": row.get("channel"),
                "mom": _comparison_facts(row.get("mom")),
                "wow": _comparison_facts(row.get("wow")),
                "drivers": [_driver_facts(d) for d in (row.get("drivers") or [])[:3]],
            }
        )
    return {
        "metric_mode": mode,
        "account": (report.get("account") or {}).get("name"),
        "overall": _comparison_facts((report.get("overall") or {}).get("mom")),
        "campaigns": campaigns,
    }


def _comparison_facts(comp: dict[str, Any] | None) -> dict[str, Any] | None:
    if not comp:
        return None
    cur = comp.get("current") or {}
    prev = comp.get("previous") or {}
    return {
        "current_metric": cur.get("metric_value"),
        "previous_metric": prev.get("metric_value"),
        "change_pct": comp.get("change_pct"),
        "current_cost": cur.get("cost"),
        "previous_cost": prev.get("cost"),
        "current_conversions": cur.get("conversions"),
        "previous_conversions": prev.get("conversions"),
        "current_clicks": cur.get("clicks"),
        "previous_clicks": prev.get("clicks"),
        "current_cpc": cur.get("cpc"),
        "previous_cpc": prev.get("cpc"),
        "current_conv_rate": cur.get("conv_rate"),
        "previous_conv_rate": prev.get("conv_rate"),
    }


def _driver_facts(driver: dict[str, Any]) -> dict[str, Any]:
    current = driver.get("current") or {}
    previous = driver.get("previous") or {}
    return {
        "keyword": driver.get("keyword"),
        "match_type": driver.get("match_type"),
        "current_cost": current.get("cost"),
        "previous_cost": previous.get("cost"),
        "current_conversions": current.get("conversions"),
        "previous_conversions": previous.get("conversions"),
        "current_clicks": current.get("clicks"),
        "previous_clicks": previous.get("clicks"),
        "cost_change": driver.get("cost_change"),
        "conversion_change": driver.get("conversion_change"),
    }


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise
        data = json.loads(match.group(0))
    return data if isinstance(data, dict) else {}
