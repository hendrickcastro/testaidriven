# ADR-017 — Run snapshot, re-run, resume and re-evaluate (as built)

- **Status**: Accepted — §5 (`partial` not assigned) superseded by [ADR-021](ADR-021-post-f7-hardening.md)
- **Date**: 2026-09-24
- **Supersedes**: [ADR-008](ADR-008-run-snapshot-and-rerun.md)

## Context

ADR-008 defined the snapshot, three re-run scopes, a `partial` status with resume, and left `Run.rerun_items` / `Run.snapshot_mode` as pending fields. The implementation added those fields, an "only failed" shortcut, a repetitions override, a re-evaluation of a single result, and resolves resume without a `partial` state.

## Decision

1. **Snapshot**: `RunnerService.create_run` freezes a `RunSnapshot` with full copies of the selected tasks, model profiles, rulesets, agent and the judge profile configured at that moment. Results store `task_version`; execution, scoring and the run/result pages read the snapshot, never the current entities ([P5](../../00-overview/constitution.md)).
2. **Re-run** — `create_rerun(run_id, scope, task_slugs=, model_names=, result_ids=, use_current_versions=, repetitions=)` always creates a **new** run with `rerun_of`, `rerun_scope`, `rerun_items`, `snapshot_mode` and name `"<original> · re-run[ (tasks|results)]"`:
   - `full` — every task × model of the original.
   - `tasks` — the snapshot filtered to `task_slugs` and/or `model_names` (empty = all); `rerun_items` = the slugs followed by the model names.
   - `results` — the (task, model) pairs of `result_ids`; `rerun_items` = those result ids.
   - `repetitions` defaults to the original's; items = selected pairs × repetitions.
   - The UI adds **"Only failed"** = scope `results` with every `error`/`timeout`/`refused` result id and 1 repetition; the result page's *Re-run this* preselects that result.
3. **Snapshot mode**: `original` (default) reuses a deep copy of the original snapshot; if that snapshot has **no judge**, the currently configured judge is used (the judge evaluates, it does not generate). `current` replaces tasks and profiles by their current stored versions (falling back to the snapshot copy if deleted) and uses the current judge (or the snapshot's).
4. The original run is never modified; re-runs can be chained. The re-run page links to the original and to `/compare?runs=<original>,<rerun>`.
5. **Resume**: `start(run_id)` on an existing run executes every result that is not terminal (`pending`/`running`). Cancelling puts in-flight results back to `pending`, so *Resume* continues a cancelled run; a run left `running` by a crashed process is resumed the same way. The `partial` status exists in `RunStatus` but is not assigned.
6. **Re-evaluate** (`reevaluate(result_id)`): re-runs the snapshot task's checks on the stored response and artifacts and, for `done` results, the judge (current judge, else snapshot judge) — no call to the evaluated model; the user score is untouched.
7. Comparison pairs results by task slug within `(run, model)` series ([../../03-modules/comparison.md](../../03-modules/comparison.md)); there is no automatic flag for version changes between paired snapshots.

## Alternatives considered

- **Storing every entity version**: the snapshot already keeps exactly the versions that matter.
- **A dedicated `partial` state set at startup**: not needed, since any non-terminal result is resumable; it would only improve the label shown for crashed runs.

## Consequences

- (+) Any run is reproducible and comparable regardless of later edits; retrying failures is cheap.
- (−) Snapshots duplicate task content (including base64 attachments) per run.
- (−) After a crash, a run keeps showing status `running` until it is resumed or deleted.
- (−) Pairs whose `task_version` differs between compared runs are not flagged automatically.
