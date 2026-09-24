# Module: Comparison

`services/comparison.py` (`compare`) and the *Compare* page (`/compare?runs=<id>,<id>`). Makes results comparable **per task** and **overall**, between models of one run, between runs, and between an original run and its re-runs ([ADR-017](../01-architecture/adr/ADR-017-run-snapshot-and-rerun-as-built.md)). Pure computation over stored results — no model calls.

## Series and pairing

- A **series** is one `(run, model_name)` pair (`Series.key = "<run_id>::<model_name>"`; label = model name, or `"<run name> · <model>"` when several runs are selected). Series are ordered by the selected runs, then by model name. An optional model filter restricts the series.
- The **first series is the baseline**; every other series gets deltas against it.
- Rows are task slugs (in order of first appearance). For each series a `TaskCell` aggregates that task's repetitions: `n`, statuses, mean `final` (failed results count as 1.0, skipped excluded), mean `auto`/`judge`/`user`, mean latency/input/output tokens over `done` results, mean cost, result ids.
- A task absent from a series has `n = 0` and is shown as "—" (never dropped). Deltas (`series − baseline`) exist for `final`, `auto`, `judge`, `latency_ms`, `output_tokens`, `cost_usd` when both sides have a value.
- Each series has a `ModelAggregate` and dimensions computed with `compute_dimensions` over **all series in view**, so performance is relative to the current set ([evaluation.md](evaluation.md)).

## Page

- Selectors: runs (multi, ordered, preselected from `?runs=` or the latest run), model filter, per-task metric (`final`, `auto`, `judge`, latency, output tokens, cost). Hint: "The first series is the baseline".
- **Radar** of the four dimensions per series and **cost vs quality** scatter (total cost vs mean final).
- **Overall table** per series: mean final, delta vs baseline, reliability, performance, conformity, intelligence, success rate, stdev, pass@k, latency p50/p95, tokens in/out, cost; *Export CSV*.
- **Per-task chart**: grouped bars of the selected metric per task and series.
- **Per-task table**: one cell per task × series with the metric (linking to the first result), the delta coloured green/red (higher is better for scores; lower is better for latency, tokens and cost) and the non-`done` statuses when present.
- **Original vs re-run**: a re-run's page links to `/compare?runs=<original>,<rerun>`; tasks not re-run show "—" on the re-run side.
- CSV export: one row per task × series (`task, series, n, final, auto, judge, user, latency_ms, input_tokens, output_tokens, cost_usd, delta_final_vs_baseline`). The run page also exports all its results as CSV/JSON.

## Business rules

- Comparisons are computed from stored results only; editing a task or profile never changes a past comparison.
- Performance normalization is relative to the series in view.
- Skipped results are excluded from averages but their status is visible.

## Acceptance criteria

- [x] Two runs of the same task set show two series and a per-task delta of 0 for identical answers (`test_runner.py::test_rerun_single_task_and_results`).
- [ ] A task only in run B appears with "—" for run A.
- [ ] Original vs re-run with scope `results` shows the re-run tasks with deltas and the others as "—".
- [ ] Changing a user score changes the comparison on the next refresh of the page.
- [ ] CSV export opens in a spreadsheet with one row per task × series.
