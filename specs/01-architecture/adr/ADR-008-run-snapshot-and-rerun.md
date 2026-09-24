# ADR-008 — Run snapshot and re-run (full, tasks or selected results)

- **Status**: Superseded by [ADR-017](ADR-017-run-snapshot-and-rerun-as-built.md) (2026-09-24)
- **Date**: 2026-09-24

## Context

Tasks, model profiles, rulesets and agents are editable and versioned (`version`), but the repository keeps only their latest version. A run executed last month must still be interpretable and comparable after its tasks changed. Users also need to repeat a battery: to measure stability, to verify a provider update, or to retry only what failed (timeouts, errors) — without re-paying for the whole battery.

## Decision

1. **Snapshot**: `create_run` freezes a `RunSnapshot` with full copies of the selected tasks, model profiles, rulesets, agent and judge. Results reference `task_version`; comparisons and scoring read the snapshot, never current entities ([P5](../../00-overview/constitution.md)).
2. **Re-run** always creates a **new** `Run` with `rerun_of = <original run id>` and `rerun_scope`:
   - `full` — every item of the original.
   - `tasks` — all models × repetitions for a subset of tasks (and optionally a subset of models).
   - `results` — exactly the selected results (task × model pairs, e.g. "all with status error/timeout").
3. By default the re-run **reuses the original snapshot** (filtered to the selection). The option **"with current versions"** rebuilds the snapshot from the current entities; the UI then shows which tasks/profiles changed version and comparisons flag the pairs whose `task_version` differs.
4. The original run is never modified. Re-runs can be chained (`rerun_of` points to the run it was launched from).
5. **Resume** is different from re-run: it continues the **same** run, executing only results still `pending`/`running` (after a crash or app restart, status `partial`).
6. Comparison supports **original vs re-run** per task and overall, pairing results by `Result.key` (`task_slug::model_name`) and aggregating repetitions ([../../03-modules/comparison.md](../../03-modules/comparison.md)).

## Alternatives considered

- **Store every entity version in its own collection**: full history, but more documents and queries; the snapshot already keeps exactly the versions that matter.
- **Re-run by overwriting results in place**: cheaper, but destroys the evidence needed for stability and regression analysis.

## Consequences

- (+) Any run is reproducible and comparable forever, regardless of later edits.
- (+) Retrying only failed items is cheap and keeps the original intact.
- (−) Snapshots duplicate task content per run (including attachments); large snapshots are spilled to the artifact store by the Firestore adapter.
- (−) The `Run` entity needs two extra fields to record the selection: `rerun_items` (result keys) and `snapshot_mode` (`original`/`current`) — **to be added to `models.py`** (see [../../02-data-model/firestore-collections.md](../../02-data-model/firestore-collections.md)).
