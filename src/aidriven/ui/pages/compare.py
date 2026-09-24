"""Compare runs/models: overall per dimension and per task, with deltas against a baseline."""

from __future__ import annotations

import csv
import io
from typing import Any

from nicegui import ui

from aidriven.domain.models import Result, Run
from aidriven.services.comparison import Comparison, compare
from aidriven.ui.charts import grouped_bars, radar, scatter_cost_quality
from aidriven.ui.common import ctx, fmt_cost, fmt_dt, fmt_ms, fmt_num, frame, section
from aidriven.ui.i18n import t

METRICS = {
    "final": ("score.final", True),
    "auto": ("score.auto", True),
    "judge": ("score.judge", True),
    "latency_ms": ("metric.latency", False),
    "output_tokens": ("metric.output_tokens", False),
    "cost_usd": ("metric.cost", False),
}


def _fmt_metric(metric: str, v: float | None) -> str:
    if metric == "latency_ms":
        return fmt_ms(v)
    if metric == "cost_usd":
        return fmt_cost(v)
    return fmt_num(v, 2)


def _delta_class(metric: str, d: float | None) -> str:
    if d is None or abs(d) < 1e-9:
        return "text-grey-7"
    higher_better = METRICS[metric][1]
    good = d > 0 if higher_better else d < 0
    return "text-positive" if good else "text-negative"


def page(runs_param: str | None = None) -> None:
    c = ctx()
    all_runs = c.repo.find(Run)
    preselected = [r for r in (runs_param or "").split(",") if r] or [r.id for r in all_runs[:1]]
    state: dict[str, Any] = {"runs": preselected, "models": [], "metric": "final"}
    with frame("nav.compare"), section(t("compare.title"), t("compare.caption")):
        with ui.row().classes("w-full items-end"):
            run_sel = (
                ui.select(
                    {r.id: f"{r.name} · {fmt_dt(r.created_at)}" for r in all_runs},
                    label=t("compare.runs"),
                    value=[r for r in preselected if any(x.id == r for x in all_runs)],
                    multiple=True,
                    with_input=True,
                )
                .classes("flex-[3]")
                .props("use-chips")
            )
            model_names = sorted({m.name for r in all_runs for m in r.snapshot.models})
            model_sel = (
                ui.select(model_names, label=t("compare.models_filter"), multiple=True, clearable=True)
                .classes("flex-[2]")
                .props("use-chips")
            )
            metric_sel = ui.select(
                {k: t(v[0]) for k, v in METRICS.items()}, label=t("compare.metric"), value="final"
            ).classes("w-48")
        ui.label(t("compare.hint")).classes("text-caption text-grey-7")

        def update(_: Any = None) -> None:
            state["runs"] = list(run_sel.value or [])
            state["models"] = list(model_sel.value or [])
            state["metric"] = metric_sel.value
            body.refresh()

        for el in (run_sel, model_sel, metric_sel):
            el.on_value_change(update)

        @ui.refreshable
        def body() -> None:
            runs = [r for r in all_runs if r.id in state["runs"]]
            runs.sort(key=lambda r: state["runs"].index(r.id))
            if not runs:
                ui.label(t("compare.pick_runs"))
                return
            results = {r.id: c.repo.find(Result, where={"run_id": r.id}) for r in runs}
            cmp = compare(runs, results, state["models"] or None)
            if not cmp.series:
                ui.label(t("compare.no_data"))
                return
            render(cmp, state["metric"])

        body()


def render(cmp: Comparison, metric: str) -> None:
    with ui.row().classes("w-full items-start no-wrap max-lg:flex-wrap"):
        with ui.card().classes("flex-1 min-w-[360px]"):
            ui.label(t("compare.dimensions")).classes("text-subtitle1")
            ui.echart(radar([(s.label, s.aggregate) for s in cmp.series])).classes("w-full h-96")
        with ui.card().classes("flex-1 min-w-[360px]"):
            ui.label(t("compare.cost_quality")).classes("text-subtitle1")
            ui.echart(
                scatter_cost_quality([(s.label, s.aggregate.cost_usd, s.aggregate.mean_final or 0) for s in cmp.series])
            ).classes("w-full h-96")
    overall_table(cmp)
    with ui.card().classes("w-full"):
        ui.label(t("compare.per_task_chart", metric=t(METRICS[metric][0]))).classes("text-subtitle1")
        cats = [r.task_slug for r in cmp.rows]
        series = {s.label: [getattr(r.cells[s.key], metric) for r in cmp.rows] for s in cmp.series}
        y_max = 10 if METRICS[metric][1] else None
        ui.echart(grouped_bars(cats, series, y_max=y_max)).classes("w-full h-[420px]")
    per_task_table(cmp, metric)


def overall_table(cmp: Comparison) -> None:
    base = cmp.series[0].aggregate
    rows = []
    for s in cmp.series:
        a = s.aggregate
        row = {
            "series": s.label,
            "final": fmt_num(a.mean_final),
            "reliability": fmt_num(a.dims.get("reliability")),
            "performance": fmt_num(a.dims.get("performance")),
            "conformity": fmt_num(a.dims.get("conformity")),
            "intelligence": fmt_num(a.dims.get("intelligence")),
            "success": f"{a.success_rate:.0%}",
            "stdev": fmt_num(a.score_stdev),
            "pass": f"{a.pass_at_k:.0%}" if a.pass_at_k is not None else "—",
            "p50": fmt_ms(a.latency_p50_ms),
            "p95": fmt_ms(a.latency_p95_ms),
            "tokens": f"{fmt_num(a.input_tokens)} / {fmt_num(a.output_tokens)}",
            "cost": fmt_cost(a.cost_usd),
            "delta": "—"
            if s is cmp.series[0] or a.mean_final is None or base.mean_final is None
            else f"{a.mean_final - base.mean_final:+.2f}",
        }
        rows.append(row)
    cols = [
        ("series", "compare.series"),
        ("final", "score.final"),
        ("delta", "compare.delta_baseline"),
        ("reliability", "dim.reliability"),
        ("performance", "dim.performance"),
        ("conformity", "dim.conformity"),
        ("intelligence", "dim.intelligence"),
        ("success", "runs.success"),
        ("stdev", "compare.stdev"),
        ("pass", "runs.pass_at_k"),
        ("p50", "runs.latency_p50"),
        ("p95", "runs.latency_p95"),
        ("tokens", "runs.tokens_io"),
        ("cost", "runs.cost"),
    ]
    with ui.card().classes("w-full overflow-auto"):
        ui.label(t("compare.overall")).classes("text-subtitle1")
        ui.label(t("compare.baseline", name=cmp.series[0].label)).classes("text-caption text-grey-7")
        ui.table(
            columns=[
                {"name": k, "label": t(lbl), "field": k, "align": "left" if k == "series" else "right"}
                for k, lbl in cols
            ],
            rows=rows,
            row_key="series",
        ).classes("w-full").props("dense flat")
        ui.button(t("compare.export_csv"), icon="download", on_click=lambda: export_csv(cmp)).props("flat")


def per_task_table(cmp: Comparison, metric: str) -> None:
    with ui.card().classes("w-full overflow-auto"):
        ui.label(t("compare.per_task")).classes("text-subtitle1")
        with ui.element("table").classes("w-full text-sm border-collapse"):
            with ui.element("thead"), ui.element("tr"):
                with ui.element("th").classes("text-left p-2 border-b"):
                    ui.label(t("common.task"))
                for s in cmp.series:
                    with ui.element("th").classes("p-2 border-b"):
                        ui.label(s.label)
            with ui.element("tbody"):
                for row in cmp.rows:
                    with ui.element("tr").classes("hover:bg-gray-100 dark:hover:bg-neutral-800"):
                        with ui.element("td").classes("p-2 border-b font-mono text-xs"):
                            ui.label(row.task_slug)
                        for s in cmp.series:
                            cell = row.cells[s.key]
                            with ui.element("td").classes("p-2 border-b text-center"):
                                if cell.n == 0:
                                    ui.label("—").classes("text-grey-5")
                                    continue
                                value = getattr(cell, metric)
                                link = f"/results/{cell.result_ids[0]}" if cell.result_ids else None
                                if link:
                                    ui.link(_fmt_metric(metric, value), link).classes("font-medium")
                                d = row.deltas.get(s.key, {}).get(metric)
                                if d is not None:
                                    ui.label(
                                        f"{d:+.2f}" if metric not in ("latency_ms",) else f"{d / 1000:+.1f}s"
                                    ).classes(f"text-caption {_delta_class(metric, d)}")
                                if any(st not in ("done",) for st in cell.statuses):
                                    ui.label(", ".join(sorted(set(cell.statuses)))).classes("text-caption text-warning")


def export_csv(cmp: Comparison) -> None:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "task",
            "series",
            "n",
            "final",
            "auto",
            "judge",
            "user",
            "latency_ms",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "delta_final_vs_baseline",
        ]
    )
    for row in cmp.rows:
        for s in cmp.series:
            cell = row.cells[s.key]
            w.writerow(
                [
                    row.task_slug,
                    s.label,
                    cell.n,
                    cell.final,
                    cell.auto,
                    cell.judge,
                    cell.user,
                    cell.latency_ms,
                    cell.input_tokens,
                    cell.output_tokens,
                    cell.cost_usd,
                    row.deltas.get(s.key, {}).get("final"),
                ]
            )
    ui.download.content(buf.getvalue(), "aidriven-comparison.csv", "text/csv")
