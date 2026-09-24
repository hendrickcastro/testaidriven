"""Scores and the four dimensions (reliability, performance, conformity, intelligence).

Formulas are documented in specs/03-modules/evaluation.md; keep both in sync.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from aidriven.domain.models import CheckResult, Result, ResultStatus

FAIL_STATUSES = (ResultStatus.ERROR, ResultStatus.REFUSED, ResultStatus.TIMEOUT)


def auto_score(checks: list[CheckResult]) -> float | None:
    """0..10 from weighted check scores; skipped checks don't count. None if nothing was evaluable."""
    scored = [c for c in checks if not c.skipped]
    total = sum(c.weight for c in scored)
    if not scored or total <= 0:
        return None
    return round(10 * sum(c.score * c.weight for c in scored) / total, 2)


def dimension_rate(checks: list[CheckResult], dimension: str) -> float | None:
    scored = [c for c in checks if not c.skipped and c.dimension == dimension]
    total = sum(c.weight for c in scored)
    return sum(c.score * c.weight for c in scored) / total if total > 0 else None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


@dataclass
class ModelAggregate:
    """Aggregate of one model's results (within a run, or across a comparison)."""

    model_name: str
    n_results: int = 0
    n_done: int = 0
    n_error: int = 0
    n_refused: int = 0
    n_timeout: int = 0
    n_skipped: int = 0
    n_reviewed: int = 0
    success_rate: float = 0.0
    mean_final: float | None = None
    mean_auto: float | None = None
    mean_judge: float | None = None
    mean_user: float | None = None
    score_stdev: float | None = None  # mean per-task stdev across repetitions
    pass_at_k: float | None = None  # share of tasks where at least one repetition fully passed its checks
    pass_all_k: float | None = None  # share of tasks where every repetition fully passed
    conformity_rate: float | None = None
    intelligence_rate: float | None = None
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    ttft_p50_ms: float | None = None
    tokens_per_s: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    reasoning_estimated: bool = False
    cost_usd: float = 0.0
    dims: dict[str, float | None] = field(default_factory=dict)  # 0..10 per dimension


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 3) if values else None


def aggregate(model_name: str, results: list[Result]) -> ModelAggregate:
    a = ModelAggregate(model_name=model_name)
    considered = [r for r in results if r.status != ResultStatus.SKIPPED]
    a.n_results = len(considered)
    a.n_skipped = len(results) - len(considered)
    a.n_done = sum(r.status == ResultStatus.DONE for r in considered)
    a.n_error = sum(r.status == ResultStatus.ERROR for r in considered)
    a.n_refused = sum(r.status == ResultStatus.REFUSED for r in considered)
    a.n_timeout = sum(r.status == ResultStatus.TIMEOUT for r in considered)
    a.n_reviewed = sum(r.score.reviewed for r in considered)
    a.success_rate = a.n_done / a.n_results if a.n_results else 0.0

    finals = [r.score.final for r in considered]
    # Failed results count as the minimum score so errors can't inflate the mean.
    a.mean_final = _mean(
        [
            f if f is not None else 1.0
            for f, r in zip(finals, considered, strict=True)
            if f is not None or r.status in FAIL_STATUSES
        ]
    )
    a.mean_auto = _mean([r.score.auto for r in considered if r.score.auto is not None])
    a.mean_judge = _mean([r.score.judge for r in considered if r.score.judge is not None])
    a.mean_user = _mean([r.score.user for r in considered if r.score.user is not None])

    by_task: dict[str, list[Result]] = defaultdict(list)
    for r in considered:
        by_task[r.task_slug].append(r)
    stdevs, any_pass, all_pass, tasks_with_checks = [], 0, 0, 0
    for reps in by_task.values():
        scores = [r.score.final if r.score.final is not None else 1.0 for r in reps]
        if len(scores) > 1:
            stdevs.append(statistics.pstdev(scores))
        if any(r.checks for r in reps):
            tasks_with_checks += 1
            full = [r.status == ResultStatus.DONE and (r.score.auto or 0) >= 9.999 for r in reps]
            any_pass += any(full)
            all_pass += all(full)
    a.score_stdev = _mean(stdevs)
    if tasks_with_checks:
        a.pass_at_k = any_pass / tasks_with_checks
        a.pass_all_k = all_pass / tasks_with_checks

    all_checks = [c for r in considered for c in r.checks]
    a.conformity_rate = dimension_rate(all_checks, "conformity")
    a.intelligence_rate = dimension_rate(all_checks, "intelligence")

    ok = [r for r in considered if r.status == ResultStatus.DONE]
    lat = [r.metrics.latency_ms for r in ok if r.metrics.latency_ms]
    a.latency_p50_ms = percentile(lat, 0.5)
    a.latency_p95_ms = percentile(lat, 0.95)
    a.ttft_p50_ms = percentile([r.metrics.ttft_ms for r in ok if r.metrics.ttft_ms], 0.5)
    tps = [r.metrics.output_tokens_per_s for r in ok if r.metrics.output_tokens_per_s]
    a.tokens_per_s = _mean(tps)
    a.input_tokens = sum(r.metrics.input_tokens for r in considered)
    a.output_tokens = sum(r.metrics.output_tokens for r in considered)
    a.reasoning_tokens = sum(r.metrics.reasoning_tokens for r in considered)
    a.reasoning_estimated = any(r.metrics.reasoning_estimated for r in considered)
    a.cost_usd = round(sum(r.metrics.cost_usd for r in considered), 6)
    return a


def compute_dimensions(aggs: list[ModelAggregate]) -> None:
    """Fill `dims` (0..10) for each aggregate. Performance is relative to the best model in the set."""
    lat_best = min((a.latency_p50_ms for a in aggs if a.latency_p50_ms), default=None)
    cost_per = {a.model_name: (a.cost_usd / a.n_done) if a.n_done else None for a in aggs}
    positive_costs = [c for c in cost_per.values() if c]
    cost_best = min(positive_costs) if positive_costs else None
    for a in aggs:
        consistency = 1.0 - min((a.score_stdev or 0.0) / 4.5, 1.0)
        reliability = 10 * (0.7 * a.success_rate + 0.3 * consistency) if a.n_results else None
        perf_parts = []
        if lat_best and a.latency_p50_ms:
            perf_parts.append(lat_best / a.latency_p50_ms)
        c = cost_per[a.model_name]
        if cost_best and c:
            perf_parts.append(cost_best / c)
        elif c == 0 and a.n_done:
            perf_parts.append(1.0)  # free model
        performance = 10 * statistics.fmean(perf_parts) if perf_parts else None
        conformity = 10 * a.conformity_rate if a.conformity_rate is not None else None
        intelligence = a.mean_final
        a.dims = {
            "reliability": round(reliability, 2) if reliability is not None else None,
            "performance": round(performance, 2) if performance is not None else None,
            "conformity": round(conformity, 2) if conformity is not None else None,
            "intelligence": round(intelligence, 2) if intelligence is not None else None,
        }


def aggregate_by_model(results: list[Result]) -> list[ModelAggregate]:
    groups: dict[str, list[Result]] = defaultdict(list)
    for r in results:
        groups[r.model_name].append(r)
    aggs = [aggregate(name, rs) for name, rs in sorted(groups.items())]
    compute_dimensions(aggs)
    return aggs
