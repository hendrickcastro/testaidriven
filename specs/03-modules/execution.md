# Module: Execution (runs)

`services/runner.py` (`RunnerService`, `LiveRun`) and `services/agent.py` (`AgentRunner`). Turns a suite, a quick-run selection or a re-run request into a `Run`, executes its items concurrently, records everything ([P8](../00-overview/constitution.md)) and supports cancel, resume, re-run and re-evaluation ([ADR-017](../01-architecture/adr/ADR-017-run-snapshot-and-rerun-as-built.md)).

## Creating a run

- `create_run(name=, task_ids=, model_ids=, ruleset_ids=, agent_id=, repetitions=1, concurrency=4, task_timeout_s=900, judge_enabled=True, suite_id=None)`:
  1. Loads the tasks, profiles, rulesets and agent by id (missing ids are silently dropped); at least one task and one model are required (`ValueError`).
  2. Freezes the `RunSnapshot` with the currently configured judge (`AppContext.judge_profile()`: `judge.enabled` and `judge.model_id`).
  3. Creates one `Result` per repetition × task × model (`repetition` 0-based). If the task `requires` a capability the model lacks (`vision`, `tools`, `json_mode`), the result is created `skipped` with `error = "model lacks required capability: …"`. `total_items` = all results; `done_items` starts at the number of skipped ones.
- `create_from_suite(suite, name=None)` — name `"<suite> · <YYYY-mm-dd HH:MM>"` (UTC).
- `create_rerun(run_id, scope=, task_slugs=, model_names=, result_ids=, use_current_versions=, repetitions=)` — see [Re-run](#re-run).
- Parameter validation and provider availability are checked **per item at execution time**, not at creation.

## Executing an item (`process(run, result, task, profile)`)

1. Marks the result `running` (`started_at`, saved).
2. `profile.validate_params()` must be empty, else `error` "invalid parameters: …" (no call made). The adapter must resolve (connection exists and is enabled), else `error` "provider not available: …".
3. **Request** (`prompting.build_request`): system prompt as in [rules-agents.md](rules-agents.md); user message = text documents (`<document name="…">…</document>` blocks, before the prompt) + task prompt + delivery instructions when `artifact_names` is set; image attachments as image parts only if the profile has `vision`; profile params deep-copied; `metadata = {task_slug, run_id, repetition}` (used only by the fake oracle).
4. **Call** under `asyncio.wait_for(…, run.task_timeout_s)`: if the snapshot has an agent **and** the profile has `tools`, the agent loop ([rules-agents.md](rules-agents.md)); otherwise one `adapter.generate(request)` (a profile without tools runs single-shot even when the run has an agent). Streaming is used when `params.stream` is set (the adapters decide per provider; see [../05-llm/llm-port.md](../05-llm/llm-port.md)).
5. **Classify**:
   - `timeout` — `task_timeout_s` exceeded; `error "no answer within <s>s"`; nothing from the call is kept.
   - `error` — `LLMError` (non-retryable, or retries exhausted); `error` holds the adapter message. Also invalid params / provider unavailable (above) and unexpected exceptions (`internal error: …`).
   - `refused` — any response has `refusal=True`, or no files were extracted and the text looks like a refusal (`< 600` chars starting with *I'm sorry / I can't help / I won't / Lo siento / No puedo ayudar…*). Checks still run; `auto` becomes 0 if the task has checks; no judge.
   - `done` — an answer was received (even if wrong).
6. **Metrics** (`_metrics`): tokens summed over all model calls of the item; `latency_ms` = sum of call latencies; `ttft_ms` of the first call; `output_tokens_per_s = output_tokens / ((latency_ms − ttft_ms)/1000)` (rounded to 2) when positive; `cost_usd = pricing.cost(input, output, cached)` rounded to 6; `retries` summed; `turns` = number of calls; `tool_calls` from the agent loop.
7. **Post-processing** for `done`/`refused`: `response_text` (agent: final text), `reasoning_text` (all calls), `finish_reason` (last call); artifacts extracted from all call texts plus agent files, stored ([artifacts-sandbox.md](artifacts-sandbox.md)); checks → `Score.auto`; judge (only `done`) → `Score.judge` ([evaluation.md](evaluation.md)).
8. `_finish` sets status, `error`, `finished_at` and saves the result.

## Concurrency, retries and ordering

- `start(run_id)` launches `_execute` as an asyncio task (`LiveRun`: total, done, active items, last 300 events). Pending results are ordered by `(repetition, task order in the snapshot, model name)` and run under `asyncio.Semaphore(run.concurrency)`.
- Retries for transient provider errors happen inside the adapters ([../05-llm/llm-port.md](../05-llm/llm-port.md)); their count lands in `Metrics.retries`.
- Sandbox checks of one result run sequentially; there is no separate container limit (parallelism is bounded by the run concurrency).
- After every finished item the run's `done_items` is updated and the run document saved. One crashing item never stops the run.

## Run lifecycle

| Status | Meaning |
|---|---|
| `pending` | Created, not started |
| `running` | `_execute` in progress (the UI shows "running" only while `is_running` is true, or the stored status) |
| `completed` | Every pending result was processed |
| `cancelled` | `cancel(run_id)` (or app shutdown): in-flight results go back to `pending`, not-started ones stay `pending` |
| `failed` | An unexpected exception escaped the worker pool; `Run.error` set |
| `partial` | Set at startup by `bootstrap.recover_interrupted_runs` for runs left `running` by a process that died (`error = "interrupted: …"`); their `running` results go back to `pending` — **resumable** |

- **Resume** (*Resume* button, visible unless `completed`): `start(run_id)` again executes every result that is not terminal. This continues cancelled and `partial` runs.
- **Delete** (blocked while running): deletes each artifact from its store, then artifact metadata, results and the run.

## Re-run

`create_rerun` builds a new run linked by `rerun_of` (never touching the original):

- `full` — all tasks × models; `tasks` — snapshot filtered by `task_slugs` and/or `model_names`; `results` — the (task, model) pairs of `result_ids`.
- `repetitions` override (default: the original's); UI **"Only failed"** = scope `results` over all `error`/`timeout`/`refused` results with 1 repetition.
- `use_current_versions=False` → deep copy of the original snapshot (current judge only if the snapshot had none); `True` → current tasks/profiles (snapshot copy if deleted) and the current judge.
- Recorded fields: `rerun_scope`, `rerun_items` (result ids, or slugs + model names), `snapshot_mode`.

## Re-evaluate and review

- `reevaluate(result_id, rejudge=True)` — re-runs the snapshot task's checks on the stored response and artifacts (loaded from their stores) and, for `done` results, the judge (current judge, else snapshot judge). No model call.
- `set_user_score(result_id, score | None, comment)` and `accept_judge(result_id)` — see [evaluation.md](evaluation.md).

## Business rules

- A run never changes its snapshot; results are only updated within the run.
- Wrong answers are `done` with low scores; `error` is reserved for failing to obtain an answer.
- Judge calls do not count towards the evaluated model's metrics or cost.
- `task_timeout_s` bounds the model call(s) of an item (including agent tool execution), not the checks or the judge.

## Acceptance criteria

- [x] A run of 2 tasks × 2 fake profiles × 2 repetitions completes with 8 results, scores, artifacts and a judge score (`test_runner.py::test_full_run_with_oracle_and_echo`).
- [x] A task requiring vision on a profile without vision yields a `skipped` result counted as done (`test_runner.py::test_skip_when_capability_missing`).
- [x] Invalid parameters produce an `error` result without calling the model (`test_runner.py::test_invalid_params_error_without_calling_model`).
- [x] Re-running scope `tasks` and `results` (with a repetitions override) and `full` creates linked runs with the expected items; the comparison shows a zero delta for the identical oracle answer (`test_runner.py::test_rerun_single_task_and_results`).
- [x] The agent loop with the fake `tools` model executes `run_python` in the sandbox and records `turns = 2`, `tool_calls = 1` (`test_runner.py::test_agent_loop_with_tools`).
- [x] The fake `flaky` model fails ~30 % of prompts on the first attempt and recovers on retry (`test_infra.py::test_flaky_fake_recovers_on_retry`).
- [x] A run left `running` with a `running` result is recovered at startup as `partial` with the result back to `pending` (`test_runner.py::test_interrupted_runs_are_recovered`).
- [ ] Cancelling a running run leaves unfinished results `pending`; *Resume* completes only those.
- [ ] `ttft_ms` is recorded for streaming profiles and `None` for non-streaming ones.
