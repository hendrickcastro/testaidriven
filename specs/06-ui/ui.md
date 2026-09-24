# UI

NiceGUI local web app (`python -m aidriven` → `http://127.0.0.1:8731`, [ADR-010](../01-architecture/adr/ADR-010-nicegui-ui-as-built.md)). Mental model: **configure → run → review → compare**. Pages use the `AppContext` and services ([../04-api/services.md](../04-api/services.md)).

## Layout and i18n

- `common.frame(title_key)`: header with the app name and tagline, a **backend badge** (SQLite / Firestore, green when Firestore), a **sandbox badge** (green "docker" / orange "process" / red **"Sandbox: Docker down"** when the mode is `docker` but the daemon is unreachable — keys `header.sandbox_down`/`header.sandbox_down_tip`; with tooltip), the **theme button** (cycles auto → light → dark; `ui.dark_mode`, stored per browser in `app.storage.user["theme"]` and in `AppConfig.theme`; tooltip keys `theme.*`) and the **language selector**; a left drawer (toggle button) with the 11 navigation entries; page title as `h5`.
- **Languages**: English (default) and Spanish. Every visible string comes from `ui/locales/{en,es}.json` via `t(key)` ([ADR-018](../01-architecture/adr/ADR-018-ui-i18n-as-built.md)); changing the language (header or *Settings*) saves `data/config.json`, stores it in the per-browser `app.storage.user` and reloads the page. User data is shown as is.
- **Dialogs are attached to the page root** (`common.root_dialog()`), so they survive refreshes of the table that opened them; destructive actions go through `common.confirm()`.
- Tables are Quasar tables with icon-button action slots (`common.action_slot`); errors surface as notifications (`notify_error`).
- **Light/dark theme**: colour classes are Tailwind classes with `dark:` variants (no light-only Quasar colour classes); charts (`ui/charts.py`) use neutral text/axis colours on a transparent background ([ADR-023](../01-architecture/adr/ADR-023-generated-locales-and-theme.md)).
- UI strings are edited in `tools/gen_locales.py` (`key → (en, es)`), which regenerates both JSON locales (514 keys).

## Pages and routes (registered in `app.py`)

| Route | Page (en / es) | Content |
|---|---|---|
| `/` | Dashboard / Panel | KPIs (tasks, models, suites, runs); warnings (process sandbox, no judge, local-only data, only fake models); last 8 runs with status and progress; leaderboard + radar of the latest completed run with pending-review link |
| `/models` | Models / Modelos | **Profiles** table on top (*Edit*, *Clone*, *Use as judge*, *Test*, *Delete*, import/export JSON); below, **one expandable card per provider kind** (`catalog.PROVIDERS`) with connected / not configured, key-source and profile-count badges, key and docs links, connection form (API key, base URL only where relevant, *Advanced*: name/timeout/retries/enabled/API version/headers; *Save*, *Test*, *Remove key*, *Delete*), **Recommended models** (catalog rows with price/context/vision/reasoning/tools badges and one-click *Add*) and **All models from the provider** (live list cached 10 min, filter, *Add*); extra connections for Azure OpenAI and OpenAI-compatible. Dropdown-driven profile editor (model select + refresh, reasoning/budget/max-tokens/sampling presets, capabilities auto-detected with *Re-detect* and *Override manually*, prices auto) — [models-config.md](../03-modules/models-config.md) |
| `/tasks` | Tasks / Tareas | Table with search and category/source filters, selection; editor tabs General/Prompt/Checks (YAML + *Try checks*)/Judge/Attachments; *Quick run* (row or selection); import (paste/upload) and export YAML/JSON — [tasks.md](../03-modules/tasks.md) |
| `/rules` | Rules and agents / Reglas y agentes | Rulesets (name, description, position, content) and agents (persona, tools, max turns, tool timeout) — [rules-agents.md](../03-modules/rules-agents.md) |
| `/suites` | Batteries / Baterías | Suites table; editor with task shortcuts (all enabled / per category / clear), models, rulesets, agent, repetitions, concurrency, timeout, judge switch and executions estimate; *Run* — [battery.md](../03-modules/battery.md) |
| `/runs` | Runs / Ejecuciones | Runs table (status, progress, tasks, models, re-run of, created) with *Open*, *Re-run*, *Delete* (blocked while running); auto-refresh every 3 s while a run is active |
| `/runs/{run_id}` | Run detail | Status badge, progress bar and info; live box (active items, last 12 events) refreshed every 1 s; *Resume*, *Cancel*, *Re-run* dialog (whole battery / some tasks-models / only failed / specific results, repetitions, "use current versions"), CSV/JSON export; re-run banner with links to the original and to the comparison; KPIs (results, cost, tokens, pending review); per-model table (final, four dimensions, success, pass@k, latency p50, tokens/s, tokens, reasoning tokens, cost) + radar; **task × model matrix** with one chip per repetition (score colour, outlined = not reviewed, tooltip with auto/judge/user/error) — [execution.md](../03-modules/execution.md) |
| `/results/{result_id}` | Result detail | Tabs Response (rendered markdown; `file=` info strings shown as captions) / Raw / Reasoning / Task (prompt + reference) / Transcript (JSON); artifacts panel with viewers; scoring card (auto/judge/user/final, slider 1–10 step 0.5, comment, *Save score*, *Accept judge*, *Clear*, *Re-evaluate*, *Re-run this*); metrics card; checks card (expandable details); judge card (criteria, rationale, same-model warning, judge tokens and cost) — [evaluation.md](../03-modules/evaluation.md) |
| `/review` | Review / Revisión | Run selector, "only pending" switch, progress counter; one result at a time: rendered response, auto/judge scores, failed checks, judge rationale, slider + comment; *Save & next*, *Accept judge*, *Skip*, *Previous*, link to the full result |
| `/compare?runs=a,b` | Compare / Comparar | Runs (ordered, baseline first), model filter, metric; radar, cost-vs-quality scatter, overall table with delta vs baseline + CSV export, per-task grouped bars, per-task table with coloured deltas — [comparison.md](../03-modules/comparison.md) |
| `/artifacts` | Artifacts / Artefactos | Run selector; one expansion per artifact with *Download*, *Run in sandbox* (Python), *Render headless* (HTML), *Preview*/*Source* tabs — [artifacts-sandbox.md](../03-modules/artifacts-sandbox.md) |
| `/settings` | Settings / Configuración | **General** (language, data dir) · **Firestore** (status, upload service-account JSON, database id, *Test connection*, *Activate* / *Deactivate*, *Migrate local → Firestore*) · **Storage** (cloud switch, bucket, prefix, *Save*, *Test storage*) · **Judge** (enabled, profile) · **Sandbox** (status, mode auto/docker/process, images, memory, CPUs, pids, default timeout, *Save*, *Test sandbox*) · **Local data** (*Backup* to `data/backups/`) — [persistence-firestore.md](../03-modules/persistence-firestore.md) |
| `/logs` | Logs / Registros | Index hints (copy/open/dismiss); log viewer with level filter, search, auto-refresh, clear — [logs.md](../03-modules/logs.md) |

## HTML artifact preview

`common.sandboxed_iframe(html)` renders `<iframe sandbox="allow-scripts" referrerpolicy="no-referrer" srcdoc="…">` (520 px high) — **never** `allow-same-origin`, `allow-top-navigation`, `allow-popups` or `allow-forms`, and no URL pointing to the app ([P4](../00-overview/constitution.md)).

## Live updates

- Run detail: `ui.timer(1.0)` reads the run and the in-memory `LiveRun`; the body (KPIs, tables, matrix) is re-rendered only when `done_items` or the running state changes.
- Runs list and Logs: `ui.timer(3.0)`.
- Score changes reload the result page; the comparison and dashboard reflect them on their next render.

## UX principles

- Show only what the model supports: parameter fields follow the capability switches.
- Every number is traceable: matrix chips and per-task cells link to the result; the result page shows auto/judge/user/final side by side.
- Destructive actions (delete run, task, profile, connection, suite, ruleset, agent) ask for confirmation.
- Secrets are never displayed; key fields are write-only.

## Acceptance criteria

- [x] Every UI key used in `ui/` exists in `en.json` and `es.json`, both locales have the same keys and placeholders (`test_i18n.py`).
- [ ] All 11 navigation pages (plus run and result detail) render in English and Spanish, in light and dark theme.
- [ ] Switching the language to Spanish translates labels but leaves task prompts and model outputs untouched.
- [ ] A running run updates its progress and matrix without page reload.
- [ ] The HTML preview iframe has `sandbox="allow-scripts"` and no `allow-same-origin` (DOM assertion in a UI test).
- [ ] The header sandbox badge shows "process" (orange) whenever the process fallback is active, and "Sandbox: Docker down" (red) with mode `docker` and no daemon.
