"""ECharts option builders shared by run detail, compare and dashboard.

Colours are neutral so charts read well in light and dark mode (the theme may be "auto", unknown server-side).
"""

from __future__ import annotations

from typing import Any

from aidriven.services.scoring import ModelAggregate
from aidriven.ui.i18n import t

PALETTE = ["#4c6ef5", "#f08c00", "#2f9e44", "#e03131", "#7048e8", "#1098ad", "#d6336c", "#5c940d", "#868e96", "#ae3ec9"]
DIMS = ("reliability", "performance", "conformity", "intelligence")
MUTED = "#8a8f98"
GRID = "rgba(138,143,152,0.25)"


def _axis(**extra: Any) -> dict[str, Any]:
    axis_label = {"color": MUTED, **extra.pop("axisLabel", {})}
    return {
        "axisLabel": axis_label,
        "axisLine": {"lineStyle": {"color": MUTED}},
        "splitLine": {"lineStyle": {"color": GRID}},
        "nameTextStyle": {"color": MUTED},
        **extra,
    }


def _base(**extra: Any) -> dict[str, Any]:
    return {
        "backgroundColor": "transparent",
        "color": PALETTE,
        "textStyle": {"color": MUTED},
        "legend": {"bottom": 0, "type": "scroll", "textStyle": {"color": MUTED}},
        **extra,
    }


def radar(series: list[tuple[str, ModelAggregate]]) -> dict[str, Any]:
    return _base(
        tooltip={},
        radar={
            "indicator": [{"name": t(f"dim.{d}"), "max": 10} for d in DIMS],
            "radius": "65%",
            "axisName": {"color": MUTED},
            "splitLine": {"lineStyle": {"color": GRID}},
            "axisLine": {"lineStyle": {"color": GRID}},
            "splitArea": {"show": False},
        },
        series=[
            {
                "type": "radar",
                "data": [
                    {
                        "name": label,
                        "value": [round(agg.dims.get(d) or 0, 2) for d in DIMS],
                        "areaStyle": {"opacity": 0.1},
                    }
                    for label, agg in series
                ],
            }
        ],
    )


def grouped_bars(
    categories: list[str], series: dict[str, list[float | None]], y_max: float | None = 10, y_name: str = ""
) -> dict[str, Any]:
    return _base(
        tooltip={"trigger": "axis"},
        grid={"left": 40, "right": 16, "top": 24, "bottom": 80, "containLabel": True},
        xAxis=_axis(type="category", data=categories, axisLabel={"rotate": 35, "interval": 0, "fontSize": 10}),
        yAxis=_axis(type="value", max=y_max, name=y_name),
        series=[{"name": name, "type": "bar", "data": values, "barMaxWidth": 26} for name, values in series.items()],
    )


def scatter_cost_quality(points: list[tuple[str, float, float]]) -> dict[str, Any]:
    """points = (label, cost_usd, mean_score)."""
    return _base(
        tooltip={"trigger": "item"},
        grid={"left": 50, "right": 20, "top": 20, "bottom": 60},
        xAxis=_axis(type="value", name=t("compare.cost_axis"), nameLocation="middle", nameGap=28),
        yAxis=_axis(type="value", name=t("compare.score_axis"), max=10, min=0),
        series=[
            {"name": label, "type": "scatter", "symbolSize": 16, "data": [[round(cost, 6), round(score, 2)]]}
            for label, cost, score in points
        ],
    )
