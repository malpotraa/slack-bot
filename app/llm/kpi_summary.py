"""Agent-written Google Ads KPI explanations from deterministic fact packs.

The model only NARRATES. Every figure it is allowed to mention — cost,
conversions, CPC, conversion rate, CTR, and every period-over-period
percentage, at both the campaign and the keyword-driver level — is precomputed
here in deterministic Python, so the model never does arithmetic. After the
model replies, `explain_kpi_report` runs a numeric reconciliation pass: any
note containing a `$` / `%` / decimal figure that does not trace back to that
item's fact pack (within a rounding tolerance) is dropped. A dropped note
degrades to the deterministic WOW/MOM rows — it is never a wrong number.
"""

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


_PROMPT = (
    "You write concise Google Ads performance explanations for Slack.\n"
    "You are given a JSON fact pack. Each campaign and the account total has\n"
    "`mom` (month-to-date vs the same dates last month) and `wow` (last 7 days\n"
    "vs the prior 7); Search campaigns also have keyword `drivers`.\n"
    "\n"
    "HARD RULES:\n"
    "- Explain the MOM change for each item in ONE sentence.\n"
    "- Every number you write — every $ value, %, CPC, conversion rate, CTR —\n"
    "  MUST appear verbatim in that item's facts. Copy numbers; NEVER add,\n"
    "  subtract, multiply, divide, or otherwise compute a number yourself.\n"
    "- Percentage changes are precomputed in each `change` block (cost_pct,\n"
    "  conversions_pct, cpc_pct, conv_rate_pct, ctr_pct, metric_pct). Quote\n"
    "  those. `cpc` is money; `conv_rate` and `ctr` are ALREADY percentages.\n"
    "- Do not invent keywords, causes, dates, or numbers. Name a keyword only\n"
    "  if it appears in that campaign's `drivers`.\n"
    "- Prefer 'visible driver' / 'correlated with' over claiming causation.\n"
    "- CPA / cost per conversion: lower is better. ROAS: higher is better.\n"
    "- If an item has no clear driver, give a brief metric-level sentence or\n"
    "  return an empty string for it.\n"
    "- Keep each explanation to one sentence, <=180 characters when possible.\n"
    'Return JSON only: {"overall":"...","campaigns":{"<id>":"..."}}.\n\n'
    "FACTS:\n"
)


async def explain_kpi_report(report: dict[str, Any]) -> dict[str, str]:
    """Return short, reconciliation-checked explanations keyed by campaign id
    plus `overall`.

    The model gets only precomputed metrics and driver facts. Any note whose
    numbers do not trace back to the fact pack is dropped. On any failure the
    function returns {} and callers fall back to the deterministic rows.
    """
    fact_pack = _compact_fact_pack(report)
    if not fact_pack["campaigns"] and not fact_pack.get("overall"):
        return {}

    prompt = _PROMPT + json.dumps(fact_pack, separators=(",", ":"))
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

    overall_note = str(payload.get("overall") or "").strip()
    if overall_note:
        if _note_reconciles(overall_note, _collect_numbers(fact_pack.get("overall"))):
            out["overall"] = overall_note[:300]
        else:
            logger.warning("KPI note dropped (overall): figures did not reconcile")

    by_id = {c["id"]: c for c in fact_pack["campaigns"] if c.get("id")}
    campaigns = payload.get("campaigns")
    if isinstance(campaigns, dict):
        for campaign_id, summary in campaigns.items():
            note = str(summary or "").strip()
            if not note:
                continue
            campaign = by_id.get(str(campaign_id))
            if campaign is None:
                logger.warning(
                    f"KPI note dropped (campaign {campaign_id}): unknown id"
                )
                continue
            if _note_reconciles(note, _collect_numbers(campaign)):
                out[str(campaign_id)] = note[:300]
            else:
                logger.warning(
                    f"KPI note dropped (campaign {campaign_id}): "
                    "figures did not reconcile"
                )
    return out


# ── Deterministic fact pack ────────────────────────────────────────────────


def _compact_fact_pack(report: dict[str, Any]) -> dict[str, Any]:
    """Slim, fully-precomputed view of a KPI report for the model to narrate."""
    mode = report.get("mode") or "cpa"
    campaigns = []
    for row in (report.get("campaigns") or [])[:10]:
        campaigns.append(
            {
                "id": str(row.get("campaign_id") or ""),
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
    """A current/previous metric pair with every delta precomputed."""
    if not comp:
        return None
    current = _metrics_block(comp.get("current") or {})
    previous = _metrics_block(comp.get("previous") or {})
    change = _change_block(current, previous)
    # The headline metric (CPA or ROAS) % change is computed upstream from the
    # same metric_value — use it as the authoritative `metric_pct`.
    metric_pct = _round(comp.get("change_pct"), 2)
    if metric_pct is not None:
        change["metric_pct"] = metric_pct
    return {"current": current, "previous": previous, "change": change}


def _driver_facts(driver: dict[str, Any]) -> dict[str, Any]:
    """One keyword driver, with CPC / conv-rate / CTR and deltas precomputed."""
    current = _metrics_block(driver.get("current") or {})
    previous = _metrics_block(driver.get("previous") or {})
    return {
        "keyword": driver.get("keyword"),
        "match_type": driver.get("match_type"),
        "current": current,
        "previous": previous,
        "change": _change_block(current, previous),
    }


def _metrics_block(row: dict[str, Any]) -> dict[str, Any]:
    """Derive every metric the narration may quote from a raw metric row.

    `conv_rate` and `ctr` are returned as PERCENTAGES so what the model reads
    equals what it writes (and what the reconciliation guard checks)."""
    cost = _num(row.get("cost"))
    conversions = _num(row.get("conversions"))
    clicks = _num(row.get("clicks"))
    impressions = _num(row.get("impressions"))
    block: dict[str, Any] = {
        "cost": _round(cost, 2),
        "conversions": _round(conversions, 2),
        "clicks": int(clicks),
        "impressions": int(impressions),
        "cpc": _ratio(cost, clicks, 2),
        "conv_rate": _ratio(conversions * 100, clicks, 2),
        "ctr": _ratio(clicks * 100, impressions, 2),
    }
    metric_value = row.get("metric_value")
    if metric_value is not None:
        block["metric_value"] = _round(metric_value, 2)
    return block


_CHANGE_FIELDS = ("cost", "conversions", "clicks", "impressions", "cpc", "conv_rate", "ctr")


def _change_block(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    """Period-over-period % change for every field — so the model never has to
    compute one."""
    out: dict[str, Any] = {}
    for field in _CHANGE_FIELDS:
        pct = _pct_change(current.get(field), previous.get(field))
        if pct is not None:
            out[f"{field}_pct"] = pct
    return out


# ── Reconciliation guard ───────────────────────────────────────────────────


def _collect_numbers(obj: Any) -> set[float]:
    """Every numeric leaf in a fact-pack slice — the set a note may quote."""
    found: set[float] = set()

    def walk(node: Any) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            found.add(abs(round(float(node), 2)))
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(obj)
    return found


_NUMBER_TOKEN = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")


def _note_reconciles(note: str, allowed: set[float]) -> bool:
    """True if every money / percentage / decimal figure in `note` matches a
    fact-pack number within a rounding tolerance.

    Bare integers (counts like "from 1 to 4 conversions") are not checked — the
    hallucination risk worth guarding is money and computed percentages, and
    checking bare integers would false-drop notes over phrases like "top 3".
    """
    for token in _NUMBER_TOKEN.findall(note):
        is_money = "$" in token
        is_pct = "%" in token
        body = token.strip("$%").replace(",", "")
        if not is_money and not is_pct and "." not in body:
            continue  # bare integer — not checked
        try:
            value = abs(float(body))
        except ValueError:
            continue
        # Percentages: the model rounds in prose (58.46 -> "58.5%" / "58%").
        # Money/decimals: expected to be quoted verbatim.
        abs_tol, rel_tol = (0.7, 0.02) if is_pct else (0.05, 0.005)
        if not any(abs(value - a) <= max(abs_tol, rel_tol * a) for a in allowed):
            return False
    return True


# ── Small numeric helpers ──────────────────────────────────────────────────


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _round(value: Any, places: int) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return None


def _ratio(numerator: float, denominator: float, places: int) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator, places)


def _pct_change(current: Any, previous: Any) -> float | None:
    if current is None or previous is None:
        return None
    try:
        cur = float(current)
        prev = float(previous)
    except (TypeError, ValueError):
        return None
    if prev == 0:
        return None
    return round((cur - prev) / prev * 100, 1)


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise
        data = json.loads(match.group(0))
    return data if isinstance(data, dict) else {}
