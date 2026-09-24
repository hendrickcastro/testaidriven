# Data model — collections

Every collection carries the prefix **`aidriven_`** ([P1](../00-overview/constitution.md)). Documents are the pydantic entities of [`models.py`](../../src/aidriven/domain/models.py) serialized with `model_dump(mode="json")` (Firestore) / `model_dump_json()` (SQLite): **snake_case field names** (no aliases — so `Check.schema_` is stored as `schema_`; YAML/JSON task exports use the alias `schema`), document id = `Entity.id`, dates as **ISO-8601 strings in both backends**. SQLite keeps one table per collection with the same name ([ADR-012](../01-architecture/adr/ADR-012-sqlite-firestore-and-local-config.md)). Access always goes through `RepositoryPort`.

Every document also has `id`, `created_at`, `updated_at` (from `Entity`); they are omitted below. Generated ids are 12 hex chars (`new_id()`); entities created by the bootstrap and the seed loader use **fixed ids**: `provider_fake`, `model_fake_<echo|oracle|judge|tools>`, `rules_rigor`, `rules_concise`, `agent_python_engineer`, `suite_seed_hard`, `seed_<slug>`.

## `aidriven_providers` — `ProviderConnection` (no secrets)

```jsonc
{
  "kind": "openai",                  // ProviderKind
  "name": "OpenAI personal",
  "base_url": null,                  // required for azure_openai (resource endpoint), openai_compatible; optional otherwise
  "api_version": null,               // stored, not used by the Azure v1 endpoint
  "extra_headers": {},
  "timeout_s": 600.0,
  "max_retries": 2,
  "enabled": true
}
```

Invariant: **no field ever contains an API key**. The key lives in the keyring under `provider:<id>:api_key` ([ADR-016](../01-architecture/adr/ADR-016-secrets-as-built.md)). For `fake` connections, `base_url` optionally points to the oracle solutions folder.

## `aidriven_models` — `ModelProfile`

```jsonc
{
  "name": "GPT-5",
  "provider_id": "<aidriven_providers.id>",
  "model": "gpt-5",
  "description": "Responses API. Reasoning models reject temperature/top_p.",
  "capabilities": {
    "vision": true, "reasoning": "effort", "reasoning_efforts": ["minimal", "low", "medium", "high"],
    "reasoning_budget_max": 32000, "temperature": false, "top_p": false, "top_k": false, "seed": false,
    "stop_sequences": false, "tools": true, "json_mode": true, "streaming": true, "verbosity": true,
    "context_window": 400000, "max_output_tokens": 128000
  },
  "params": {
    "temperature": null, "top_p": null, "top_k": null, "max_output_tokens": 32000, "seed": null,
    "stop_sequences": [], "reasoning_effort": "high", "reasoning_budget": null, "reasoning_enabled": null,
    "verbosity": null, "json_mode": false, "stream": true, "extra": {}
  },
  "pricing": { "input_per_mtok": 1.25, "output_per_mtok": 10.0, "cached_input_per_mtok": 0.125 },
  "system_prompt": "",
  "tags": [],
  "version": 1
}
```

Invariants: the *Models* page refuses to save a profile whose `validate_params()` is not empty or whose `name` is already used; the runner re-validates before every call; every edit from the UI increments `version`.

## `aidriven_tasks` — `Task`

```jsonc
{
  "slug": "lisp-interpreter",        // unique (enforced by the editor and by import upsert)
  "title": "Mini-Lisp interpreter with closures and proper tail calls",
  "category": "algorithms",
  "difficulty": "extreme",           // medium | hard | extreme
  "prompt": "Implement an interpreter ... in a single Python 3.12 module named `lisp.py` ...",
  "attachments": [ { "name": "shapes_grid.png", "mime": "image/png", "path": "shapes_grid.png", "data_b64": null, "inline_text": false } ],
  "requires": [],                    // vision | tools | json_mode
  "expected_artifact": "python",     // ArtifactKind
  "artifact_names": ["lisp.py"],
  "rubric": "Score 1-10. Correctness of the evaluator semantics ...",
  "reference_answer": "A reader producing cons cells ...",   // judge only
  "checks": [
    { "id": "lisp-interpreter#0", "kind": "python_tests", "name": "hidden interpreter tests", "dimension": "intelligence",
      "weight": 1.0, "expected": null, "pattern": null, "tolerance": 1e-6, "case_sensitive": false, "schema_": null,
      "target": null, "code": "import sys\nfrom aidriven_harness import case, report, load_module ...", "files": {},
      "timeout_s": 240.0 }
  ],
  "tags": ["interpreter", "tail-calls"],
  "source": "seed",                  // seed | user | import
  "version": 1,
  "enabled": true
}
```

Invariants: only the latest version is stored — history lives in run snapshots; editing in the UI increments `version`; `Check.id` is kept across edits of a stored task; seed checks without an explicit `id` get the deterministic id **`<slug>#<index>`** (`task_io.load_seed_tasks`), so check ids are stable across seed versions.

## `aidriven_rulesets` — `Ruleset`

```jsonc
{ "name": "Rigor", "description": "Verify before answering; state assumptions.",
  "content": "Work carefully. Before giving a final answer, verify it ...", "position": "append", "version": 1 }
```

## `aidriven_agents` — `AgentDef`

```jsonc
{ "name": "Python engineer (sandbox)", "description": "", "persona": "You are a meticulous software engineer ...",
  "tools": ["write_file", "run_python", "run_html", "read_attachment"], "max_turns": 10, "tool_timeout_s": 30.0,
  "version": 1 }
```

## `aidriven_suites` — `Suite`

```jsonc
{ "name": "Hard battery (seed)", "description": "",
  "task_ids": ["seed_lisp-interpreter", "..."], "model_ids": ["model_fake_oracle", "model_fake_echo"],
  "ruleset_ids": [], "agent_id": null,
  "repetitions": 1, "concurrency": 4, "task_timeout_s": 900.0, "judge_enabled": true }
```

`repetitions` 1..50 and `concurrency` 1..64 are validated by the model.

## `aidriven_runs` — `Run`

```jsonc
{
  "name": "Hard battery (seed) · 2026-09-24 15:02",
  "suite_id": "<aidriven_suites.id>",     // null for quick runs
  "status": "running",                    // pending | running | completed | failed | cancelled | partial (interrupted, resumable)
  "snapshot": { "tasks": [ /* Task */ ], "models": [ /* ModelProfile */ ], "rulesets": [], "agent": null,
                "judge": { /* ModelProfile */ } },
  "repetitions": 1, "concurrency": 4, "task_timeout_s": 900.0, "judge_enabled": true,
  "rerun_of": null,                       // original run id when this is a re-run
  "rerun_scope": null,                    // full | tasks | results
  "rerun_items": [],                      // scope results: result ids; scope tasks: task slugs then model names
  "snapshot_mode": "original",            // original | current
  "started_at": "<iso>", "finished_at": null,
  "total_items": 4, "done_items": 1,
  "error": null, "notes": ""
}
```

Invariants: `snapshot` is not modified after creation; `total_items` = number of results created (selected task × model pairs × repetitions, skipped items included); a re-run never modifies its `rerun_of` run ([ADR-017](../01-architecture/adr/ADR-017-run-snapshot-and-rerun-as-built.md)).

## `aidriven_results` — `Result`

```jsonc
{
  "run_id": "<aidriven_runs.id>",
  "task_id": "seed_lisp-interpreter", "task_slug": "lisp-interpreter", "task_version": 1,
  "model_id": "...", "model_name": "GPT-5",
  "repetition": 0,                        // 0-based
  "status": "done",                       // pending | running | done | error | refused | timeout | skipped
  "response_text": "...", "reasoning_text": "",
  "transcript": [],                       // agent loop: {turn, role: "assistant", text, tool_calls} / {turn, role: "tool", name, output}
  "finish_reason": "completed", "error": null,
  "metrics": { "input_tokens": 1830, "output_tokens": 5120, "reasoning_tokens": 3900, "cached_tokens": 0,
               "latency_ms": 48210.0, "ttft_ms": 20450.0, "output_tokens_per_s": 184.1, "cost_usd": 0.053487,
               "retries": 0, "turns": 1, "tool_calls": 0 },
  "checks": [ { "check_id": "lisp-interpreter#0", "name": "hidden interpreter tests", "dimension": "intelligence",
                "passed": false, "score": 0.83, "weight": 1.0, "skipped": false,
                "detail": "25/30 cases passed (docker)\n- tail calls: ...", "duration_ms": 3120.0 } ],
  "judge": { "judge_model_id": "...", "judge_model_name": "Fake judge", "score": 7.0,
             "criteria": [ { "name": "correctness", "score": 7, "comment": "..." } ], "rationale": "...",
             "same_model_warning": false, "metrics": { /* Metrics of the judge call */ }, "error": null },
  "score": { "auto": 8.3, "judge": 7.0, "user": null, "user_comment": "", "reviewed": false },
  "artifact_ids": ["..."],
  "started_at": "<iso>", "finished_at": "<iso>"
}
```

Invariants: exactly one result per item of a run; `Result.key = task_slug::model_name`; `score.final` is **computed**, never stored; `check.passed` ⇔ `score ≥ 0.999`.

## `aidriven_artifacts` — `Artifact` (metadata only)

```jsonc
{ "run_id": "...", "result_id": "...", "name": "lisp.py", "kind": "python", "size": 8812,
  "sha256": "9f2c...", "storage": "local", "location": "<run>/<result>/lisp.py", "mime": "text/x-python" }
```

`location` is relative to `data/artifacts/` for `local`, and the blob path (`aidriven/<run>/<result>/<name>`) for `firebase`. Content lives in the store indicated by `storage` ([ADR-014](../01-architecture/adr/ADR-014-firebase-storage-as-built.md)).

## `aidriven_settings` — internal flags (fixed document ids)

```jsonc
// "bootstrap"          — first-start data created
{ "done": true }
// "seed_suite_known"   — seed task ids already offered to suite_seed_hard
{ "ids": ["seed_lisp-interpreter", "seed_regex-engine"] }
// "secret__provider:<connection id>:api_key" — encrypted API key (ADR-022), only while Firestore is active
{ "value_enc": "gAAAAAB…", "algo": "fernet-hkdf-sha256" }
```

Only ciphertext is stored for keys (Fernet token; key = HKDF-SHA256 of `AIDRIVEN_ENCRYPTION_KEY` or the service account's `private_key`, salt `aidriven-secrets-v1`). User configuration is **not** stored here: it lives in `data/config.json` ([ADR-012](../01-architecture/adr/ADR-012-sqlite-firestore-and-local-config.md)).

## `aidriven_health` — connection probe (Firestore only)

```jsonc
// document id "probe": written, read back and deleted by Settings > Firestore > Test connection
{ "at": "<iso>", "by": "aidriven" }
```

Invariant: normally empty; a leftover probe means a test failed between write and delete.

## `data/config.json` — `AppConfig` (local file, not a collection)

```jsonc
{
  "language": "en", "theme": "auto",
  "firestore": { "enabled": false, "credentials_path": "D:\\...\\data\\secrets\\firestore-sa.json", "project_id": "algoritxia",
                 "database_id": "(default)", "last_test_ok": true, "last_test_at": "<iso>", "last_test_detail": "write/read/delete OK on aidriven_health" },
  "storage":   { "cloud_enabled": false, "bucket": "algoritxia.firebasestorage.app", "prefix": "aidriven/" },
  "judge":     { "enabled": true, "model_id": "model_fake_judge" },
  "sandbox":   { "mode": "auto", "python_image": "python:3.12-slim",
                 "playwright_image": "aidriven-browser:1.63.0", "browser_base_image": "mcr.microsoft.com/playwright/python:v1.63.0-noble",
                 "memory_mb": 512, "cpus": 1.0, "pids_limit": 256, "default_timeout_s": 60.0 }
}
```

## Queries and indexes

Queries are designed so that **no composite index is needed** (equality filters only; ordering in memory):

| Query | Filter | Ordering / limit |
|---|---|---|
| Results of a run | `run_id ==` | newest first; pages re-sort in memory |
| Artifacts of a run / of a result | `run_id ==` / `result_id ==` | in memory |
| Seed tasks | `source == "seed"` | — |
| "Has this profile results?" (rename warning) | `model_id ==` | `limit 1` (applied after the query in Firestore) |
| Lists (providers, models, tasks, rulesets, agents, suites, runs) | none (full collection) | newest first; pages re-sort |
| Deletions of a run | `delete_where run_id ==` on results and artifacts | — |

Single-field indexes are automatic in Firestore. **Nothing is added to the shared `config/firestore.indexes.json`** and `config/firestore.rules` is not touched ([P1](../00-overview/constitution.md)). If a query ever raises `FailedPrecondition`, the error (with the index-creation URL) is logged and listed on the [Logs](../03-modules/logs.md) page; any index created that way is recorded here:

| Collection | Fields | Created | Reason |
|---|---|---|---|
| — | — | — | — |

## Firestore encoding of nested arrays

Firestore rejects arrays that directly contain arrays (e.g. `sql_result` `expected: [[1, "a"], [2, "b"]]` in seed tasks and snapshots) and reserves `__x__` field names. `repo_firestore.encode_nested` therefore stores any such list as `{"aidriven_nested_json": "<json of the list>"}` on write, recursively inside dicts and lists; `decode_nested` restores it on read (`get`, `find`). SQLite stores plain JSON. Readers of raw Firestore documents must apply the same unwrapping.

## Size limits

Firestore documents above 900,000 bytes are truncated by the adapter (`response_text`, `reasoning_text` to 225,000 chars; `transcript` to its last 5 entries) with a WARNING log; a document that still does not fit raises a clear `ValueError`; see [persistence-firestore.md](../03-modules/persistence-firestore.md). SQLite has no limit.
