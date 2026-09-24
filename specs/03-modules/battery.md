# Module: Battery (suites)

Entity `Suite` and the *Batteries* page (`/suites`). A battery defines **what runs against what**: tasks × model profiles × rulesets × optional agent × repetitions, with concurrency, timeout and the judge toggle. It is the input of [execution.md](execution.md).

## Configuration

| Field | Meaning | Limits (as implemented) |
|---|---|---|
| `task_ids` | Tasks to run | UI multi-select with search; disabled tasks are selectable and marked "(off)" |
| `model_ids` | Model profiles to compare | UI multi-select |
| `ruleset_ids` | Rulesets injected into every item, in this order | 0..n |
| `agent_id` | Agent that turns items into an agentic loop | optional |
| `repetitions` | Times each task × model pair is executed | 1..50 (model and UI) |
| `concurrency` | Maximum items in flight | 1..64 (model and UI) |
| `task_timeout_s` | Wall-clock limit per item (model calls + agent tools) | default 900; UI 10..7200 |
| `judge_enabled` | Whether the configured judge scores results of this suite | default true |

## Suite editor (dialog)

- Name, description; task picker with shortcut buttons **All enabled**, one button per category (adds that category's tasks) and **Clear**; models, rulesets and agent selectors; repetitions, concurrency, timeout; judge switch.
- Live **estimate**: `<n> executions` with `n = |tasks| × |models| × repetitions` (skipped items and cost are not estimated).
- Suites table: name, #tasks, #models, repetitions, items, agent, judge; actions **Run** (creates the run with `create_from_suite`, starts it and opens `/runs/<id>`), **Edit**, **Delete** (with confirmation).
- **Quick run** lives on the *Tasks* page: one or several selected tasks × chosen profiles × repetitions, judge switch, `Run.suite_id = None` ([tasks.md](tasks.md)).

## Built-in suite

The bootstrap creates **`suite_seed_hard`** — "Hard battery (seed)": every seed task × the fake profiles *Fake oracle* and *Fake echo*, 1 repetition, concurrency 4 — so the app can run a full battery offline at zero cost. Seed tasks added in later releases are appended to it automatically on startup (tasks the user removed from it are not re-added, tracked in `aidriven_settings/seed_suite_known`).

## Business rules

- A suite is a **template**: editing it never affects runs already created (they have their snapshot).
- Ids of deleted tasks, profiles, rulesets or agents are dropped silently: the editor shows only existing entities, and `create_run` skips missing ids (a run needs at least one task and one model).
- Order of `ruleset_ids` is significant (system prompt order, see [rules-agents.md](rules-agents.md)).
- Stability metrics (stdev, pass@k vs pass-all) need repetitions > 1; with 1 repetition the stdev is not computed.
- Items whose model lacks a capability the task requires are created as `skipped` at run creation.

## Acceptance criteria

- [ ] Creating a suite of 3 tasks × 2 profiles × 2 repetitions shows "12 executions".
- [ ] Running the built-in suite on a fresh install (started from the repository root, where the oracle finds `tests/fixtures/seed_solutions/`) completes offline with the fake oracle scoring 10 on every seed task.
- [ ] Editing a suite after running it does not change the existing run's snapshot or items.
- [ ] A suite with a profile whose parameters are invalid runs, and those items end as `error` with the validation problems.
- [ ] Quick run of a single task on one profile creates a run with `suite_id = null` and opens its page.
