# Module: Evaluation (checks, judge, review, dimensions)

`services/checks.py` (`CheckEngine`), `services/scoring.py` and `services/judge.py` (`JudgeService`). Produces the three scores of every result (`auto`, `judge`, `user`), the "pending review" state and the four dimensions per model ([ADR-015](../01-architecture/adr/ADR-015-judge-and-review-as-built.md)).

## Checks (`CheckEngine.run_all(checks, response_text, files)`)

Checks run sequentially; a crashing check yields `score 0` with `detail "check crashed: …"`; every result gets `duration_ms`. `score` is clamped to [0, 1]; **`passed = score ≥ 0.999`**; `detail` ≤ 4,000 chars.

| Kind | Where | Behaviour |
|---|---|---|
| `exact` | in-process | Extract the answer: with `pattern`, the last match (group 1 if the pattern has groups, else the whole match); otherwise the last `FINAL ANSWER:` line (`(?im)^\s*\**\s*FINAL ANSWER\s*\**\s*:\s*\**\s*(.+?)\s*\**\s*$`, surrounding backticks stripped). Compare `normalize(got)` with `normalize(expected)`: whitespace collapsed, trailing `.` stripped, casefolded unless `case_sensitive`. No answer → 0. |
| `numeric` | in-process | Same extraction; the **last** number of the answer (`,` and `_` separators removed, `−` accepted) must satisfy `abs(num − expected) ≤ tolerance`. |
| `regex` | in-process | `re.search(pattern, text)` where text = artifact `target` (missing artifact → 0) or the response. |
| `json_schema` | in-process | JSON from artifact `target`, else the last ```` ```json ```` block, else the whole response; validated with `jsonschema` Draft 2020-12; the first 5 errors go to `detail`. |
| `python_tests` / `python_verifier` | sandbox | Runs `check.code` as `_check.py` with the harness. |
| `html_playwright` | sandbox, Playwright image | Same, with `needs_browser=True`; uses `open_page`. |
| `sql_result` | sandbox | Generated runner: `_expected.json` = `check.expected`; creates an in-memory SQLite DB, runs `setup.sql` (from `check.files`) if present, executes artifact `target` (default `query.sql`) and requires `rows == expected` as lists (**order-sensitive**; one case with timeout `max(1, timeout_s − 5)`). |

**Authenticated outcome** ([ADR-019](../01-architecture/adr/ADR-019-authenticated-check-results.md)): the engine writes a random single-use nonce to `_aidriven_nonce`; the harness consumes it on import (before model code is loaded) and emits `AIDRIVEN_RESULT <nonce> {json}` / `AIDRIVEN_SKIP <nonce> {json}` straight to fd 1. Parsing, in order:

1. Only lines with the job's nonce are **authenticated**; any other `AIDRIVEN_RESULT`/`AIDRIVEN_SKIP` line is ignored and noted in the detail (*"ignored N forged result line(s) printed by the artifact"*).
2. More than one authenticated line → **tampering**, score 0.
3. One authenticated skip → `skipped=True` (score 0, excluded from scoring).
4. `timed_out` → 0 with `timeout after <s>s (<isolation>)`.
5. No authenticated result → 0 with exit code and the tail of stderr/stdout.
6. Otherwise `score` from the JSON and `detail = "<passed>/<total> cases passed (<isolation>)"` plus up to 10 failures.

Residual risk (documented in ADR-019): an artifact that deliberately digs the emitter out of the harness can forge an authenticated line; this is caught as tampering when `report()` also runs, but not if the artifact also stops the process before `report()` (e.g. `os._exit(0)` at import). Full protection needs process separation between the check and the candidate code.

## Automatic score (`Score.auto`, 0..10)

`auto_score(checks) = round(10 × Σ(score_i × weight_i) / Σ weight_i, 2)` over **non-skipped** checks; `None` if there are none (or the weights sum to 0).

Checks run for every result that obtained an answer (status `done` and `refused`). A `refused` result then gets `auto = 0` if the task has checks (else `None`). `error`, `timeout` and `skipped` results are not checked (`auto = None`).

## Judge preview (`Score.judge`, 1..10)

- Runs for `done` results when the run has `judge_enabled` and a snapshot judge. Prompt `judge.score` ([../05-llm/prompts.md](../05-llm/prompts.md)): task prompt, rubric (default *"Correctness, completeness and adherence to every instruction."*), reference answer (or *"(none provided)"*), a one-line-per-check summary (`[dimension] name: PASS | FAIL (xx%) | SKIPPED — first detail line`) and the candidate answer (≤ 60,000 chars; artifacts travel as fenced blocks inside it).
- **Blind**: no model name, provider, profile, metrics or reasoning text of the evaluated model.
- Non-streaming call with the judge profile's params and `max_output_tokens = min(max(params, 4096), judge capabilities.max_output_tokens)`. The reply is parsed from a fenced JSON block or the outermost `{…}`; `score` clamped to [1, 10]; `criteria` entries need a `name`; `rationale` ≤ 4,000 chars. Any failure → `JudgeVerdict.error`, `Score.judge = None` (no retries).
- `same_model_warning = judge.model == evaluated.model`.
- Judge usage is stored in `JudgeVerdict.metrics` (never added to the evaluated model's cost).

## User review (`Score.user`, 1..10)

- `Score.final` = `user` if set, else `round((judge + auto)/2, 2)` if both, else whichever exists, else `None`.
- **Pending review** = a `done` result with `reviewed == false`.
- Actions (`RunnerService.set_user_score` / `accept_judge`): *Save score* (1..10, 0.5 steps, comment) → `user`, `reviewed=true`; *Accept judge* → `user = judge`, `reviewed=true`; *Clear* → `user=None`, `reviewed=false`. Scores outside 1..10 are rejected.
- *Re-evaluate* (`reevaluate`) recomputes checks, `auto` and (for `done` results) the judge; it never modifies `user`.

## Dimensions (per model of a run, or per series of a comparison)

`scoring.aggregate(model_name, results)` builds a `ModelAggregate`; `compute_dimensions(aggs)` fills `dims`. **Skipped results are excluded** from everything (counted in `n_skipped`).

Aggregate inputs:

- `success_rate` = `done / considered` (considered = non-skipped results).
- `mean_final` = mean of `final` over results that have a final score **plus failed results (`error`/`refused`/`timeout`) counted as 1.0**; `done` results with no score are ignored.
- `score_stdev` = mean over tasks with > 1 repetition of the population stdev of `final` (missing final counted as 1.0).
- `pass_at_k` = share of tasks with checks where **at least one** repetition is `done` with `auto ≥ 9.999`; `pass_all_k` = share where **every** repetition is.
- `conformity_rate` / `intelligence_rate` = weighted mean check score of that dimension over all non-skipped checks of the model.
- Latency p50/p95 (linear interpolation), TTFT p50 and mean tokens/s over `done` results; token and cost sums over all considered results.

| Dimension | Formula (0..10) |
|---|---|
| **Reliability** | `10 × (0.7 × success_rate + 0.3 × consistency)`, `consistency = 1 − min(score_stdev / 4.5, 1)` (a missing stdev counts as 0 → consistency 1); `None` without considered results |
| **Performance** | `10 × mean(parts)` relative to the **set being compared**: `best_latency_p50 / latency_p50` and `best_cost_per_done / cost_per_done` (cost per done = `cost_usd / n_done`; a model with cost 0 and ≥ 1 done result gets part 1.0); parts that cannot be computed are omitted; `None` if none |
| **Conformity** | `10 × conformity_rate`; `None` when the model has no conformity checks |
| **Intelligence** | `mean_final` (so failures pull it down as 1.0) |

All values are rounded to 2 decimals. With a single model in the set, performance is 10 by construction.

## Business rules

- The judge never decides alone: the dashboard, run KPIs and *Review* show pending-review counts; the user's score wins.
- Weights and formulas are constants in `services/scoring.py` and documented here; changing them requires updating this spec.
- Aggregates and dimensions are recomputed on read from stored results; only the `Score` fields are persisted.

## Acceptance criteria

- [x] A `python_tests` check with one of two cases passing gives score 0.5 and names the failing case (`test_checks.py::test_python_tests_partial_credit_in_sandbox`).
- [x] `exact` normalizes case/whitespace/trailing dot; `numeric` honours tolerance; the last `FINAL ANSWER` wins (`test_checks.py`).
- [x] `json_schema` validates the last JSON block; `sql_result` compares ordered rows; a missing artifact scores 0 (`test_checks.py`).
- [x] A skipped check (authenticated `AIDRIVEN_SKIP`) is marked `skipped` (`test_checks.py::test_skip_marker_excludes_check`).
- [x] A fake result line printed by the artifact is ignored and reported as forged; the artifact cannot read the nonce; an artifact that calls the harness emitter itself produces a second authenticated line → tampering, 0 (`test_checks.py::test_forged_result_line_from_artifact_is_ignored`, `test_artifact_cannot_read_the_nonce`, `test_artifact_introspecting_harness_is_flagged`).
- [x] Setting a user score changes `final` and marks the result reviewed (`test_runner.py::test_user_score_overrides`).
- [x] With the fake oracle and echo, the oracle has `pass_all_k = 1.0`, echo `pass_at_k = 0.5`, and the oracle's intelligence is higher (`test_runner.py::test_full_run_with_oracle_and_echo`).
- [ ] A task with checks weighted 3 and 1 where only the first passes gives `auto = 7.5`.
- [ ] The judge prompt captured from the fake provider contains no evaluated model name or provider.
- [ ] Using the same model as judge and evaluated sets `same_model_warning` and shows the warning.
- [ ] Radar values for reliability, performance, conformity and intelligence match the formulas above in a unit test with fixed results.
