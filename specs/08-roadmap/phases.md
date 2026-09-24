# Roadmap by phases

Each phase delivers something usable end to end and closes with its acceptance criteria and a green test suite (`ruff`, `mypy`, `pytest`). **Phases F0–F7 are implemented** (status as of 2026-09-24); the module specs describe the as-built behaviour and ADR-010…ADR-018 record where it departs from the original plan; ADR-019…ADR-021 record the post-F7 hardening (authenticated check results, locally built browser image, log redaction, interrupted-run recovery). Remaining work is listed under [Next](#next).

## Phase 0 — Foundations

- [x] **Status: implemented.**

- `specs/`, `pyproject.toml` (hatchling, dev extras), ruff/mypy/pytest config, `.pre-commit-config.yaml`, `.github/workflows/ci.yml` (lint, format, types, tests on 3.12/3.14), `.env.example`.
- `domain/models.py` with `Run.rerun_items`, `Run.snapshot_mode` and `CheckResult.skipped`; `ports.py`; `config.py` (`EnvSettings`, `AppConfig` in `data/config.json`); `context.py` (`AppContext`) as composition root ([ADR-011](../01-architecture/adr/ADR-011-ports-and-adapters-as-built.md), [ADR-012](../01-architecture/adr/ADR-012-sqlite-firestore-and-local-config.md)).
- Logging with rotation, in-memory capture and Firestore index hints; *Logs* page ([logs.md](../03-modules/logs.md)).
- `SecretStorePort` with keyring, memory fallback and env fallback ([ADR-016](../01-architecture/adr/ADR-016-secrets-as-built.md)).
- NiceGUI shell: navigation, backend/sandbox badges, i18n `en`/`es` with header and Settings selectors ([ADR-018](../01-architecture/adr/ADR-018-ui-i18n-as-built.md)).

Deviations: services return English messages instead of i18n keys; startup options via env vars only. Log redaction was added after F7 ([ADR-021](../01-architecture/adr/ADR-021-post-f7-hardening.md)).

## Phase 1 — Models

- [x] **Status: implemented.**

- Adapters: `AnthropicAdapter`, `OpenAIResponsesAdapter` (OpenAI + Azure v1), `ChatCompletionsAdapter` (OpenRouter + OpenAI-compatible), `GeminiAdapter`, `OllamaAdapter`, `FakeAdapter` ([llm-port.md](../05-llm/llm-port.md)).
- Connection test (list + tiny generation) with latency, live discovery, built-in catalog of capabilities and prices.
- *Models* page: connections and capability-driven profiles, *Clone*, *Use as judge*, *Test*, import/export ([models-config.md](../03-modules/models-config.md)).
- Parameter-mapping tests per adapter (`test_adapters.py`).

Deviations: no *Try it* playground (the profile *Test* action sends a fixed prompt); no shared LLM contract suite.

## Phase 2 — Persistence

- [x] **Status: implemented.**

- `SqliteRepository` (table per collection) and `FirestoreRepository` (named app, equality filters, in-memory sorting, truncation above 900 KB) ([persistence-firestore.md](../03-modules/persistence-firestore.md)).
- *Settings > Firestore*: upload JSON, test (probe in `aidriven_health`), activate/deactivate, migrate local → Firestore; SQLite backup.
- "Index required" capture with copy/open buttons.

Verified: Firestore test against `algoritxia` passed on 2026-09-24. Deviations: no memory repository; no spill to the artifact store; no Firestore → local migration.

## Phase 3 — Tasks, rules, agents and batteries

- [x] **Status: implemented** (seed battery still growing).

- Task CRUD with YAML checks editor, *Try checks*, attachments, import/export YAML/JSON, quick run; seed battery with reference solutions and assets generator ([tasks.md](../03-modules/tasks.md)).
- Rulesets and agent definitions ([rules-agents.md](../03-modules/rules-agents.md)).
- Suite editor with task shortcuts and executions estimate; built-in suite `suite_seed_hard` ([battery.md](../03-modules/battery.md)).

Deviations: no capability matrix or cost upper bound in the suite editor; import upserts by slug (no skip/copy options); no public export mode.

## Phase 4 — Runner and artifacts

- [x] **Status: implemented.**

- `RunnerService`: snapshot, concurrency, adapter retries, streaming/TTFT, cancel, resume (restart of non-terminal results), re-run (full / tasks / results / only failed, original or current versions, repetitions override), re-evaluate ([execution.md](../03-modules/execution.md), [ADR-017](../01-architecture/adr/ADR-017-run-snapshot-and-rerun-as-built.md)).
- Metrics and cost; artifact extraction; local and Firebase Storage stores with the cloud toggle ([ADR-014](../01-architecture/adr/ADR-014-firebase-storage-as-built.md)).
- *Runs* list, run detail with live matrix and per-model dimensions, result page.

Interrupted runs are recovered at startup as `partial` (added after F7, [ADR-021](../01-architecture/adr/ADR-021-post-f7-hardening.md)). Deviation: Firebase Storage unusable until the bucket exists.

## Phase 5 — Sandbox, checks, judge and review

- [x] **Status: implemented.**

- `DockerSandbox` and `ProcessSandbox` with the stdlib harness; lazy image pull and local build of the browser image `aidriven-browser:1.63.0` ([ADR-020](../01-architecture/adr/ADR-020-browser-sandbox-image-built-locally.md)); *Test sandbox* probe; Docker integration tests ([artifacts-sandbox.md](../03-modules/artifacts-sandbox.md), [ADR-013](../01-architecture/adr/ADR-013-docker-sandbox-as-built.md)).
- `CheckEngine` for all eight check kinds (nonce-authenticated `AIDRIVEN_RESULT` / `AIDRIVEN_SKIP` lines, [ADR-019](../01-architecture/adr/ADR-019-authenticated-check-results.md); generated `sql_result` runner); `Score.auto`.
- `JudgeService` (blind, JSON verdict, same-model warning, own metrics); *Review* queue and result scoring card ([evaluation.md](../03-modules/evaluation.md), [ADR-015](../01-architecture/adr/ADR-015-judge-and-review-as-built.md)).
- *Artifacts* page: run Python in the sandbox, sandboxed HTML preview, headless render.

## Phase 6 — Comparison and dashboards

- [x] **Status: implemented.**

- Four dimensions per model/series; overall radar, cost-vs-quality scatter, overall table with deltas, per-task bars and table with deltas, original vs re-run link, CSV export ([comparison.md](../03-modules/comparison.md)).
- Dashboard with KPIs, warnings, recent runs and leaderboard.

Deviations: no box plot, heatmap, version-changed flag, reviewed-only filter or JSON export in *Compare* (run pages export CSV/JSON).

## Phase 7 — Agents and hardening

- [x] **Status: implemented** (hardening partially).

- Agent loop with sandbox tools (`run_python`, `write_file`, `run_html`, `read_attachment`), provider-native replay of assistant content (Anthropic content blocks, OpenAI output items with encrypted reasoning, Gemini `Content`), transcripts and turn metrics ([rules-agents.md](../03-modules/rules-agents.md)).
- Specs updated to the implemented behaviour (this revision); README quickstart.

Hardening done after F7: authenticated check results, log redaction (with test), interrupted-run recovery, explicit Firestore size errors, judge output budget capped by the judge model, stable seed check ids, red "Docker down" badge, `.nicegui/` git-ignored. Not done from the original plan: performance test with 1,000+ results, Playwright UI walkthrough.

## Test inventory (81 tests collected on 2026-09-24: 78 unit + 3 Docker integration; the seed reference tests take ~70 s)

| File | Covers |
|---|---|
| `test_adapters.py` | Request mapping without network: Anthropic adaptive thinking + `output_config.effort`, budget and disabled thinking, tool-result grouping and raw-content replay; OpenAI Responses reasoning/verbosity/JSON/instructions/`store=False`/images; Azure v1 base URL; OpenRouter reasoning and `top_k` in `extra_body`; Gemini thinking budget/level and seed; Ollama `think`, options and images; `profile_from_pick` from a catalog pick (ready to run) and from a discovered model (API metadata wins) |
| `test_artifacts.py` | Extraction: explicit `file=` blocks, name on the previous line, first-line comment, single-file fallback (last matching block), path-traversal flattening, last same-name block wins |
| `test_checks.py` | Final-answer extraction (last wins), number parsing, `exact`/`numeric`, `json_schema` on the last block, `python_tests` partial credit in the process sandbox, missing artifact → 0, `sql_result`, timeout reporting, `AIDRIVEN_SKIP` → skipped; forged result line ignored, artifact cannot read the nonce, harness introspection flagged as tampering |
| `test_i18n.py` | Every key used by `ui/` (literals, tuple keys, dynamic families) exists in both locales; `en`/`es` have identical keys and placeholders |
| `test_infra.py` | SQLite round-trip, filters, `delete_where`, settings; local store escape protection; secret resolution (saved > env) and masking; `validate_params`; `Pricing.cost`; index-URL capture and dedup; task export/import round-trip with versioning; seed tasks valid; logging setup idempotent; redaction of keys and private keys; fake `flaky` recovers on retry; encrypted repository secret store (ciphertext at rest, other machine decrypts, wrong key hidden, delete); Firestore nested-array encoding round-trip |
| `test_runner.py` | Full run with fake oracle/echo/judge (8 results, auto/judge scores, artifacts, pass@k, dimensions); re-run scopes `tasks`/`results`/`full` + comparison delta; skipped on missing capability; invalid params → error without calling the model; user score override; agent loop with the fake `tools` model in the sandbox; interrupted runs recovered as `partial` |
| `test_seed_reference.py` | For every one of the 14 seed tasks (28 tests): the reference solution in `tests/fixtures/seed_solutions/<slug>/` scores 1.0 on every non-skipped check, and a non-answer does not pass all checks (and is not fully skipped) |
| `tests/integration/test_docker_sandbox.py` (marker `docker`, skipped without a daemon) | No network + read-only root + non-root user + work-dir output; timeout kills the container; HTML page rendered in headless Chromium in `aidriven-browser:1.63.0` |

## Next

Realistic follow-ups, in suggested order:

1. **Enable the Firebase Storage bucket** for project `algoritxia` (Firebase console → Storage → Get started; verified missing on 2026-09-24), then *Settings > Storage > Test storage* and, only then, the cloud toggle. Consider disabling the toggle until the test passes ([ADR-014](../01-architecture/adr/ADR-014-firebase-storage-as-built.md)).
2. **Add real provider keys and run the seed battery on real models**: create connections/profiles (e.g. one `effort` model and one `budget` model), pick a real judge, run `suite_seed_hard` with ≥ 3 repetitions, review, and verify the four dimensions and costs against provider dashboards; tune task difficulty if some task is trivially solved or triggers refusals.
3. **CI workflow**: `.github/workflows/ci.yml` exists (ruff, format check, mypy, `pytest -m "not docker and not firestore"` on 3.12/3.14); push it to the remote, make it required on `master`, and add a separate `docker` job (Linux runner with Docker) for `tests/integration/`.
4. **Close the residual check-forgery risk** ([ADR-019](../01-architecture/adr/ADR-019-authenticated-check-results.md)): run the candidate's code in a separate process from the check (e.g. the harness talks to it over a pipe), so an artifact that introspects the harness and exits before `report()` cannot forge an authenticated result.
5. version-changed flag in comparisons; reviewed-only filter; Firestore → local migration.
6. Missing acceptance tests listed in the module specs (judge blindness, same-model warning, weighted `auto`, dimension formulas, Firestore contract with a recording client).

Done since the first revision of this list: API keys encrypted in Firestore ([ADR-022](../01-architecture/adr/ADR-022-encrypted-api-keys-in-firestore.md)); Firestore nested-array encoding (first activation now bootstraps correctly); redesigned *Models* page; dark mode and generated locales ([ADR-023](../01-architecture/adr/ADR-023-generated-locales-and-theme.md)); `run.sh` helper; Playwright image pinned and built locally (`aidriven-browser:1.63.0`, [ADR-020](../01-architecture/adr/ADR-020-browser-sandbox-image-built-locally.md)); authenticated check results; log redaction; `partial` recovery; retrying `flaky` fake; stable seed check ids; `.nicegui/` ignored.

## Out of scope (for now)

Multi-user access and authentication, hosted deployment, multi-judge ensembles, public leaderboards, fine-tuning, automatic task generation by LLM.
