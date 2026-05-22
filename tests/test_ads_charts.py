"""Static PNG chart rendering for Google Ads answers."""

from app.formatters.ads_charts import render_charts

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_render_bar_chart_produces_png():
    out = render_charts(
        [
            {
                "kind": "bar",
                "title": "Top campaigns by cost",
                "labels": ["Brand Search", "PMax Retail", "Shopping"],
                "values": [4210, 9840, 2100],
                "value_kind": "money",
                "currency": "$",
            }
        ]
    )
    assert len(out) == 1
    assert out[0]["png"].startswith(_PNG_MAGIC)
    assert out[0]["filename"]


def test_render_line_chart_produces_png():
    out = render_charts(
        [
            {
                "kind": "line",
                "title": "Daily spend",
                "x": [f"05-{d:02d}" for d in range(1, 20)],
                "y": [float(i * 10) for i in range(19)],
                "y_label": "Cost",
            }
        ]
    )
    assert len(out) == 1
    assert out[0]["png"].startswith(_PNG_MAGIC)


def test_render_line_with_gaps_does_not_crash():
    # None values (e.g. cost/conv on a zero-conversion day) render as gaps.
    out = render_charts(
        [{"kind": "line", "title": "Cost/conv", "x": ["a", "b", "c"], "y": [1.0, None, 3.0]}]
    )
    assert len(out) == 1
    assert out[0]["png"].startswith(_PNG_MAGIC)


def test_render_skips_unknown_kinds_without_crashing():
    out = render_charts([{"kind": "pie", "title": "nope"}])
    assert out == []
