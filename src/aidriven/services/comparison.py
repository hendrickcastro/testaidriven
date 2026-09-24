"""Comparison of runs and models: overall (per dimension) and per task. See specs/03-modules/comparison.md.

A "series" is (run, model). Results are paired across series by task slug; the first series is the baseline
and every other one gets deltas against it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from aidriven.domain.models import Result, ResultStatus, Run
from aidriven.services.scoring import ModelAggregate, aggregate, compute_dimensions

METRIC_KEYS = ("final", "auto", "judge", "latency_ms", "output_tokens", "cost_usd")


@dataclass
class Series:
    key: str
    label: str
    run_id: str
    run_name: str
    model_name: str
    aggregate: ModelAggregate


@dataclass
class TaskCell:
    n: int = 0
    statuses: list[str] = field(default_factory=list)
    final: float | None = None
    auto: float | None = None
    judge: float | None = None
    user: float | None = None
    latency_ms: float | None = None
    output_tokens: float | None = None
    input_tokens: float | None = None
    cost_usd: float | None = None
    result_ids: list[str] = field(default_factory=list)


@dataclass
class TaskRow:
    task_slug: str
    cells: dict[str, TaskCell]  # series key -> cell
    deltas: dict[str, dict[str, float | None]]  # series key -> metric -> delta vs baseline


@dataclass
class Comparison:
    series: list[Series]
    rows: list[TaskRow]
    baseline: str | None


def _mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(statistics.fmean(vals), 3) if vals else None


def _cell(results: list[Result]) -> TaskCell:
    ok = [r for r in results if r.status == ResultStatus.DONE]
    return TaskCell(
        n=len(results),
        statuses=[r.status.value for r in results],
        final=_mean(
            [
                r.score.final if r.score.final is not None else (1.0 if r.status != ResultStatus.SKIPPED else None)
                for r in results
            ]
        ),
        auto=_mean([r.score.auto for r in results]),
        judge=_mean([r.score.judge for r in results]),
        user=_mean([r.score.user for r in results]),
        latency_ms=_mean([r.metrics.latency_ms for r in ok]),
        output_tokens=_mean([float(r.metrics.output_tokens) for r in ok]),
        input_tokens=_mean([float(r.metrics.input_tokens) for r in ok]),
        cost_usd=_mean([r.metrics.cost_usd for r in results]),
        result_ids=[r.id for r in results],
    )


def compare(
    runs: list[Run], results_by_run: dict[str, list[Result]], model_filter: list[str] | None = None
) -> Comparison:
    series: list[Series] = []
    grouped: dict[str, list[Result]] = {}
    multi_run = len(runs) > 1
    for run in runs:
        by_model: dict[str, list[Result]] = {}
        for r in results_by_run.get(run.id, []):
            if model_filter and r.model_name not in model_filter:
                continue
            by_model.setdefault(r.model_name, []).append(r)
        for model_name, rs in sorted(by_model.items()):
            key = f"{run.id}::{model_name}"
            label = f"{run.name} · {model_name}" if multi_run else model_name
            series.append(Series(key, label, run.id, run.name, model_name, aggregate(model_name, rs)))
            grouped[key] = rs
    compute_dimensions([s.aggregate for s in series])

    slugs: list[str] = []
    for s in series:
        for r in grouped[s.key]:
            if r.task_slug not in slugs:
                slugs.append(r.task_slug)
    baseline = series[0].key if series else None
    rows = []
    for slug in slugs:
        cells = {s.key: _cell([r for r in grouped[s.key] if r.task_slug == slug]) for s in series}
        deltas: dict[str, dict[str, float | None]] = {}
        base = cells.get(baseline) if baseline else None
        for key, cell in cells.items():
            if key == baseline or base is None:
                continue
            deltas[key] = {}
            for metric in METRIC_KEYS:
                a, b = getattr(base, metric), getattr(cell, metric)
                deltas[key][metric] = round(b - a, 3) if a is not None and b is not None else None
        rows.append(TaskRow(slug, cells, deltas))
    return Comparison(series=series, rows=rows, baseline=baseline)
