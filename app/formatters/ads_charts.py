"""Server-side static chart rendering for Google Ads answers.

matplotlib with the headless ``Agg`` backend renders PNGs in memory. These calls
are CPU-bound and synchronous — callers MUST run `render_charts` inside
`asyncio.to_thread` so the Socket Mode event loop never stalls.

A chart spec is a plain dict:
  {"kind": "line", "title": ..., "x": [...], "y": [...], "y_label": ...}
  {"kind": "bar",  "title": ..., "labels": [...], "values": [...],
   "value_kind": "money" | "ratio" | "number", "currency": "$"}
"""

from __future__ import annotations

import io
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (must follow matplotlib.use)
from loguru import logger  # noqa: E402

_ACCENT = "#4285F4"
_MAX_LABEL = 28


def render_charts(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Render each spec to a PNG. Returns [{filename, png(bytes), title}].

    A spec that fails to render is skipped — a missing chart never blocks the
    text answer.
    """
    out: list[dict[str, Any]] = []
    for i, spec in enumerate(specs):
        try:
            kind = spec.get("kind")
            if kind == "line":
                png = _render_line(spec)
            elif kind == "bar":
                png = _render_bar(spec)
            else:
                continue
            out.append(
                {
                    "filename": spec.get("filename") or f"chart_{i + 1}.png",
                    "png": png,
                    "title": spec.get("title") or "",
                }
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"Google Ads chart render failed: {exc}")
    return out


def _trunc(text: Any) -> str:
    s = str(text or "")
    return s if len(s) <= _MAX_LABEL else s[: _MAX_LABEL - 1] + "…"


def _fmt(value: float, value_kind: str, currency: str) -> str:
    if value_kind == "money":
        return f"{currency}{value:,.0f}"
    if value_kind == "ratio":
        return f"{value:.2f}x"
    if value_kind == "percent":
        return f"{value * 100:.1f}%"
    return f"{value:,.0f}"


def _finish(fig) -> bytes:
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _render_line(spec: dict[str, Any]) -> bytes:
    x = list(spec.get("x") or [])
    # None → NaN so undefined days (e.g. cost/conv on a zero-conversion day)
    # render as a gap rather than a misleading zero.
    y = [
        float(v) if v is not None else float("nan")
        for v in (spec.get("y") or [])
    ]
    fig, ax = plt.subplots(figsize=(7.4, 3.2), dpi=120)
    ax.plot(range(len(y)), y, color=_ACCENT, linewidth=2)
    ax.fill_between(range(len(y)), y, color=_ACCENT, alpha=0.12)
    ax.set_title(spec.get("title") or "", fontsize=11, fontweight="bold", loc="left")
    if spec.get("y_label"):
        ax.set_ylabel(str(spec["y_label"]), fontsize=9)
    if x:
        step = max(1, len(x) // 6)
        idxs = list(range(0, len(x), step))
        ax.set_xticks(idxs)
        ax.set_xticklabels([str(x[i]) for i in idxs], fontsize=8)
    ax.grid(True, axis="y", alpha=0.25)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.margins(x=0.01)
    return _finish(fig)


def _render_bar(spec: dict[str, Any]) -> bytes:
    labels = [_trunc(label) for label in (spec.get("labels") or [])]
    values = [float(v or 0) for v in (spec.get("values") or [])]
    # Reverse so the largest bar sits at the top.
    labels, values = labels[::-1], values[::-1]
    value_kind = spec.get("value_kind") or "number"
    currency = spec.get("currency") or "$"

    height = max(2.2, 0.46 * len(labels) + 1.0)
    fig, ax = plt.subplots(figsize=(7.4, height), dpi=120)
    ax.barh(range(len(values)), values, color=_ACCENT, height=0.66)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_title(spec.get("title") or "", fontsize=11, fontweight="bold", loc="left")
    span = max(values) if values else 0
    for i, v in enumerate(values):
        ax.text(
            v + span * 0.01,
            i,
            _fmt(v, value_kind, currency),
            va="center",
            fontsize=8,
        )
    ax.set_xlim(0, span * 1.18 if span else 1)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="x", labelsize=8)
    return _finish(fig)
