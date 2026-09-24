# Glossary

Canonical project terms. Code, comments and specs use **exactly** these names; the class/field column is the name in [`src/aidriven/domain/models.py`](../../src/aidriven/domain/models.py) (or the module named). The Spanish column is the UI label used by the `es` locale ([ADR-018](../01-architecture/adr/ADR-018-ui-i18n-as-built.md)).

| Term | Class / field | UI label (es) | Definition |
|---|---|---|---|
| **Entity** | `Entity` | — | Base of every persisted document: `id` (12 hex chars from `new_id()`), `created_at`, `updated_at`. Stored in collection `aidriven_<Entity.collection>`. |
| **App config** | `config.AppConfig` | Configuración | User-editable settings (language, theme, Firestore, storage, judge, sandbox) persisted in `data/config.json` ([ADR-012](../01-architecture/adr/ADR-012-sqlite-firestore-and-local-config.md)). |
| **Env settings** | `config.EnvSettings` | — | Startup settings from `AIDRIVEN_*` env vars / `.env`: `data_dir`, `host`, `port` (8731), `reload`, `storage_secret`, `encryption_key`. |
| **App context** | `context.AppContext` | — | Composition root: config, secrets (`secrets` = encrypted Firestore store while Firestore is active, else `local_secrets`), repository, artifact stores, sandbox, check engine, runner, adapter cache. |
| **Encrypted secret store** | `adapters.secrets.EncryptedRepoSecretStore` | — | API keys as Fernet tokens in `aidriven_settings/secret__<key>`, keyring as local cache ([ADR-022](../01-architecture/adr/ADR-022-encrypted-api-keys-in-firestore.md)). |
| **Provider connection** | `ProviderConnection` | Conexión de proveedor | Access to one provider (`kind`, `name`, `base_url`, `api_version`, `extra_headers`, `timeout_s`, `max_retries`, `enabled`). The API key is **not** stored here: it lives in the secret store under `secret_key` = `provider:<id>:api_key`. Collection `aidriven_providers`. |
| **Provider kind** | `ProviderKind` | Tipo de proveedor | `anthropic`, `openai`, `azure_openai`, `gemini`, `openrouter`, `ollama`, `openai_compatible`, `fake`. |
| **Model profile** | `ModelProfile` | Perfil de modelo | A concrete model with its configuration (`model`, `capabilities`, `params`, `pricing`, `system_prompt`, `tags`, `version`). **The unit that is compared.** Two profiles can point to the same model with different params. Collection `aidriven_models`. |
| **Capabilities** | `Capabilities` | Capacidades | What the model supports (vision, reasoning mode and levels/budget max, temperature, top_p, top_k, seed, stop sequences, tools, JSON mode, streaming, verbosity, context window, max output). Drives which params the profile editor shows and what `validate_params()` accepts. |
| **Reasoning mode** | `ReasoningMode` | Modo de razonamiento | `none`, `effort` (discrete levels), `budget` (thinking tokens), `level` (Gemini 3 `thinking_level`), `toggle` (on/off). |
| **Generation params** | `GenerationParams` | Parámetros de generación | Values sent to the provider; `None` = not sent. `extra` is a raw passthrough merged into the provider request. |
| **Pricing** | `Pricing` | Precios | USD per million tokens (input, output, cached input). `cost(input, output, cached)` gives `Metrics.cost_usd`. |
| **Catalog** | `adapters/llm/catalog.py` | — | Built-in list of known models per provider with capabilities, prices and default params; pre-fills new profiles. |
| **Task** | `Task` | Tarea | One test: `slug`, `title`, `category`, `difficulty` (`medium`/`hard`/`extreme`), `prompt`, `attachments`, `requires`, `expected_artifact`, `artifact_names`, `rubric`, `reference_answer`, `checks`, `tags`, `source`, `version`, `enabled`. Collection `aidriven_tasks`. |
| **Seed battery** | `Task.source = "seed"` | Batería inicial | The hard tasks shipped as `src/aidriven/seed/tasks/*.yaml` (id `seed_<slug>`), synced at every start, plus the built-in suite `suite_seed_hard`. |
| **Attachment** | `Attachment` | Adjunto | Image (vision) or document of a task; by `path` (relative to `seed/assets/`) or `data_b64` (uploaded). Text documents are embedded in the prompt as `<document name="…">` blocks. |
| **Check** | `Check` / `CheckKind` | Comprobación | Automatic verification: `exact`, `regex`, `numeric`, `json_schema`, `python_tests`, `python_verifier`, `html_playwright`, `sql_result`. Each has a `dimension` (`intelligence` or `conformity`), a `weight` and a `timeout_s`. |
| **Check result** | `CheckResult` | Resultado de comprobación | Outcome of one check: `passed` (score ≥ 0.999), partial `score` 0..1, `skipped`, `detail`, `duration_ms`. |
| **Harness** | `sandbox_harness/aidriven_harness.py` | — | Stdlib-only helper copied into every sandbox job: `case`, `report`, `response_text`, `final_answer`, `read`, `exists`, `load_module`, `open_page`, `skip`. Consumes the single-use nonce file `_aidriven_nonce` on import and emits `AIDRIVEN_RESULT <nonce> {json}` or `AIDRIVEN_SKIP <nonce> {json}` directly to fd 1 ([ADR-019](../01-architecture/adr/ADR-019-authenticated-check-results.md)). |
| **Final answer** | — | Respuesta final | Last line `FINAL ANSWER: <value>` of a response (bold/backticks tolerated); what `exact`/`numeric` checks read by default. |
| **Ruleset** | `Ruleset` | Reglas | Markdown instructions injected into the system prompt (`position`: `prepend`/`append`). Collection `aidriven_rulesets`. |
| **Agent** | `AgentDef` / `AgentTool` | Agente | Persona + sandbox tools (`run_python`, `write_file`, `run_html`, `read_attachment`) + `max_turns` + `tool_timeout_s`. Only models with `tools` run the agent loop. Collection `aidriven_agents`. |
| **Battery / Suite** | `Suite` | Batería | Tasks × model profiles × rulesets × optional agent × repetitions, with concurrency, timeout and judge toggle. Collection `aidriven_suites`. |
| **Quick run** | `RunnerService.create_run` | Ejecución rápida | A run started from the *Tasks* page for one or several tasks without a suite (`Run.suite_id = None`). |
| **Run** | `Run` / `RunStatus` | Ejecución | One execution of a suite, a quick run or a re-run. Holds the `snapshot` and progress (`total_items`, `done_items`). Collection `aidriven_runs`. |
| **Snapshot** | `RunSnapshot` | Instantánea | Frozen copies of tasks, models, rulesets, agent and judge used by a run ([P5](constitution.md)). |
| **Re-run** | `Run.rerun_of`, `rerun_scope`, `rerun_items`, `snapshot_mode` | Re-ejecución | A new run that repeats another one fully (`full`), some tasks/models (`tasks`) or selected results (`results`; "only failed" is a shortcut), with the `original` or `current` snapshot. See [ADR-017](../01-architecture/adr/ADR-017-run-snapshot-and-rerun-as-built.md). |
| **Re-evaluate** | `RunnerService.reevaluate` | Re-evaluar | Re-run checks and judge on a stored answer without calling the evaluated model. |
| **Item** | — | Elemento | One unit of work of a run: task × model × repetition. Produces exactly one `Result`. |
| **Result** | `Result` / `ResultStatus` | Resultado | Outcome of one item: response, reasoning, transcript, metrics, checks, judge verdict, score, artifacts. Status `pending`, `running`, `done`, `error`, `refused`, `timeout`, `skipped`. Collection `aidriven_results`. |
| **Result key** | `Result.key` | — | `task_slug::model_name`. |
| **Metrics** | `Metrics` | Métricas | Tokens (input, output, reasoning, cached), `latency_ms`, `ttft_ms`, `output_tokens_per_s`, `cost_usd`, `retries`, `turns`, `tool_calls`. |
| **Judge** | `JudgeVerdict` / `JudgeCriterion` | Juez | Model profile configured in Settings that scores 1..10 blind, with criteria and rationale. `same_model_warning` when it is the same model id as the evaluated one. |
| **Score** | `Score` | Puntuación | `auto` (0..10 from checks), `judge` (1..10 preview), `user` (1..10, prevails), `user_comment`, `reviewed`. `final` = user, else mean(judge, auto), else whichever exists. |
| **Pending review** | `Score.reviewed = false` | Pendiente de revisión | A `done` result whose score has not been set or accepted by the user. |
| **Model aggregate** | `services.scoring.ModelAggregate` | — | Per-model (or per-series) statistics and the four dimensions. |
| **Dimensions** | `ModelAggregate.dims` | Dimensiones | **Reliability** (fiabilidad), **performance** (rendimiento), **conformity** (conformidad), **intelligence** (inteligencia); each 0..10. See [evaluation.md](../03-modules/evaluation.md). |
| **Series** | `services.comparison.Series` | Serie | One `(run, model)` pair in a comparison; the first series is the baseline. |
| **Artifact** | `Artifact` / `ArtifactKind` | Artefacto | File produced by a model (`python`, `html`, `javascript`, `json`, `sql`, `markdown`, `text`, `binary`); metadata in `aidriven_artifacts`, content in the artifact store (`local` or `firebase`). |
| **Execution record** | `ExecutionRecord` | Registro de ejecución | Output of a sandbox job: `ok`, `exit_code`, `stdout`, `stderr`, `duration_ms`, `timed_out`, `isolation` (`docker`/`process`), collected `files`, `screenshot_b64`. |
| **Sandbox** | `SandboxPort` / `SandboxManager` | Sandbox / entorno aislado | Where checks, artifact replays and agent tools run. Docker or process fallback. |
| **Port** | `ports.py` | — | Domain-facing interface that abstracts external technology. |
| **Adapter** | `adapters/*` | — | Concrete implementation of a port (e.g. `AnthropicAdapter`, `FirestoreRepository`). |
| **Internal settings** | `aidriven_settings` | — | Internal documents in the active repository: `bootstrap`, `seed_suite_known` and the encrypted API keys `secret__<key>`. Not user configuration. |
| **Provider info** | `catalog.ProviderInfo` / `PROVIDERS` | — | UI metadata per provider kind (label, description, key/docs URLs, needs key, base URL none/optional/required, multiple connections). |
