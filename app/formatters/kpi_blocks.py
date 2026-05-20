"""Slack Block Kit formatting for /kpi Google Ads reports."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.utils.slack_mrkdwn import escape_slack_text


def report_blocks(report: dict[str, Any], explanations: dict[str, str] | None = None) -> list[dict]:
    explanations = explanations or {}
    account = report.get("account") or {}
    mode = report.get("mode") or "cpa"
    metric_name = "ROAS" if mode == "roas" else "cost/conv."
    account_name = escape_slack_text(account.get("name") or "Google Ads account")

    lines: list[str] = [
        "*KPI Performance — WOW & MOM*",
        f"*{account_name}* · {metric_name}",
        _date_summary(report.get("windows") or {}),
        "",
    ]

    campaigns = (report.get("campaigns") or [])[:10]
    hidden = max(len(report.get("campaigns") or []) - len(campaigns), 0)
    for campaign in campaigns:
        lines.extend(_campaign_lines(campaign, mode, explanations.get(campaign["campaign_id"])))
        lines.append("")

    overall = report.get("overall") or {}
    lines.extend(_overall_lines(overall, mode, explanations.get("overall")))
    if hidden:
        lines.append("")
        lines.append(f"_{hidden} additional active campaigns hidden. Showing top 10 by recent cost._")

    return _section_blocks("\n".join(lines).strip())


def _campaign_lines(campaign: dict[str, Any], mode: str, explanation: str | None) -> list[str]:
    name = escape_slack_text(campaign.get("name") or "Unnamed campaign")
    lines = [f"*{name}*"]
    lines.append(f"WOW {_comparison_text(campaign.get('wow'), mode)}")
    mom = campaign.get("mom")
    if mom:
        lines.append(f"MOM {_comparison_text(mom, mode)}")
    else:
        lines.append("MOM no completed month-to-date range yet.")
    if explanation:
        lines.append(escape_slack_text(explanation))
    return lines


def _overall_lines(overall: dict[str, Any], mode: str, explanation: str | None) -> list[str]:
    lines = ["*Overall*"]
    lines.append(f"WOW {_comparison_text(overall.get('wow'), mode)}")
    mom = overall.get("mom")
    if mom:
        lines.append(f"MOM {_comparison_text(mom, mode)}")
    else:
        lines.append("MOM no completed month-to-date range yet.")
    if explanation:
        lines.append(escape_slack_text(explanation))
    return lines


def _comparison_text(comp: dict[str, Any] | None, mode: str) -> str:
    if not comp:
        return "n/a"
    cur = (comp.get("current") or {}).get("metric_value")
    prev = (comp.get("previous") or {}).get("metric_value")
    change = comp.get("change_pct")
    metric_label = "ROAS" if mode == "roas" else "change in cost/conv."
    if change is None:
        return f"n/a ({_metric(cur, mode)} vs {_metric(prev, mode)})"
    return f"{change:+.2f}% {metric_label} ({_metric(cur, mode)} vs {_metric(prev, mode)})"


def _metric(value: Any, mode: str) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if mode == "roas":
        return f"{number:.2f}x"
    return f"${number:,.2f}"


def _date_summary(windows: dict[str, Any]) -> str:
    last_7 = windows.get("last_7") or {}
    previous_7 = windows.get("previous_7") or {}
    mtd = windows.get("mtd")
    previous_mtd = windows.get("previous_mtd")
    parts = [
        f"WOW {_date_range(last_7)} vs {_date_range(previous_7)}",
    ]
    if mtd and previous_mtd:
        parts.append(f"MOM {_date_range(mtd)} vs {_date_range(previous_mtd)}")
    else:
        parts.append("MOM unavailable until the month has at least one completed day")
    return " · ".join(parts)


def _date_range(window: dict[str, str]) -> str:
    try:
        start = date.fromisoformat(window["start"])
        end = date.fromisoformat(window["end"])
    except Exception:
        return "unknown"
    if start == end:
        return start.strftime("%b %-d")
    if start.month == end.month:
        return f"{start.strftime('%b %-d')}–{end.strftime('%-d')}"
    return f"{start.strftime('%b %-d')}–{end.strftime('%b %-d')}"


def _section_blocks(text: str) -> list[dict]:
    blocks: list[dict] = []
    for chunk in _chunks(text, max_len=2800):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
    return blocks[:45]


def _chunks(text: str, *, max_len: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        candidate = para if not current else f"{current}\n\n{para}"
        if len(candidate) <= max_len:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = para
    if current:
        chunks.append(current)
    return chunks
