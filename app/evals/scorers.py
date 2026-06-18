"""Eval scorers for the Google Ads analyst.

Each scorer returns a float in [0.0, 1.0] — 1.0 = pass, 0.0 = fail.
Async scorers (no_recommendation) make a single cheap Haiku call.

Usage (offline, in app/evals/runner.py):
    from app.evals.scorers import (
        accuracy_scorer,
        no_recommendation_scorer,
        metric_compliance_scorer,
        date_range_scorer,
    )
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger


# ── Helpers ────────────────────────────────────────────────────────────────


def _all_delta_values(obj: Any) -> list[float]:
    """Flatten every numeric value from a _pct-filtered fact_pack skeleton."""
    values: list[float] = []
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, (int, float)):
                values.append(float(v))
            else:
                values.extend(_all_delta_values(v))
    elif isinstance(obj, list):
        for item in obj:
            values.extend(_all_delta_values(item))
    return values


def _extract_pct_mentions(text: str) -> list[float]:
    """Extract all numeric magnitudes that appear before a % sign in the text.

    Captures both positive ("25%") and negative-framed ("dropped 10.3%")
    mentions; we compare magnitudes so direction is handled by the scorer.
    """
    return [float(m) for m in re.findall(r"(\d+(?:\.\d+)?)\s*%", text)]


def _near(a: float, b: float, tol: float = 0.3) -> bool:
    return abs(abs(a) - abs(b)) <= tol


# ── Scorers ────────────────────────────────────────────────────────────────


def accuracy_scorer(reply: str, fact_pack_deltas: dict) -> float:
    """Fraction of percentage mentions in the reply that exist in the fact_pack.

    A reply that fabricates percentages not in the fact_pack scores < 1.0.
    A reply with no percentages scores 0.5 (ambiguous — neither good nor bad).
    """
    mentioned = _extract_pct_mentions(reply)
    if not mentioned:
        return 0.5

    actual = _all_delta_values(fact_pack_deltas)
    if not actual:
        # Fact pack has no deltas — can't verify. Score neutral.
        return 0.5

    mismatches = [p for p in mentioned if not any(_near(p, a) for a in actual)]
    return round(1.0 - len(mismatches) / len(mentioned), 3)


async def no_recommendation_scorer(reply: str) -> float:
    """LLM-as-judge: 1.0 if no recommendations/advice, 0.0 if any found.

    Uses a single Haiku call — cheap (~$0.0001 per eval row).
    """
    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic()
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Does the following reply give any recommendations, advice, "
                        "suggestions, or tell the user what they should do?\n\n"
                        f"{reply}\n\n"
                        "Answer YES or NO only."
                    ),
                }
            ],
        )
        answer = resp.content[0].text.strip().upper()
        return 0.0 if "YES" in answer else 1.0
    except Exception as exc:
        logger.warning(f"no_recommendation_scorer failed: {exc}")
        return 0.5


def metric_compliance_scorer(reply: str, metadata: dict) -> float:
    """1.0 if ROAS/conversion-value mentions match the requested metric mode.

    ROAS and conversion value must not appear unless the tool was called with
    metric='roas'. The tool_args are stored in metadata.input.tool_args.
    """
    tool_args = (metadata.get("input") or {}).get("tool_args") or {}
    use_roas = tool_args.get("metric") == "roas"
    has_roas_mention = bool(re.search(r"\bROAS\b|conversion value", reply, re.IGNORECASE))
    if has_roas_mention and not use_roas:
        return 0.0
    return 1.0


def date_range_scorer(reply: str) -> float:
    """1.0 if the reply mentions a reporting period, 0.0 if it jumps straight to numbers."""
    patterns = [
        r"last\s+\d+\s+days?",
        r"\d{4}-\d{2}-\d{2}",
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\b",
        r"this\s+(week|month|quarter|year)",
        r"past\s+(week|month|30|90)",
        r"week[- ]over[- ]week|month[- ]over[- ]month|year[- ]over[- ]year",
    ]
    return 1.0 if any(re.search(p, reply, re.IGNORECASE) for p in patterns) else 0.0
