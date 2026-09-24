# ADR-010 — NiceGUI local web UI (as built)

- **Status**: Accepted
- **Date**: 2026-09-24
- **Supersedes**: [ADR-001](ADR-001-nicegui-local-web-ui.md)

## Context

ADR-001 chose NiceGUI and described a UI that subscribes to progress events, is configured with `--host/--port` flags and uses `ui.aggrid`/`ui.codemirror`. The implementation (phases F0–F7) kept NiceGUI but made simpler choices for live updates, startup configuration and components. This ADR records what was built so the spec matches the code.

## Decision

1. **NiceGUI ≥ 3** (developed on 3.17) is the UI framework. `python -m aidriven` (or the `aidriven` console script) builds the `AppContext`, runs the bootstrap and calls `ui.run(host, port, reload, storage_secret, show=False)`.
2. **Startup configuration only through environment variables** (`AIDRIVEN_HOST`, default `127.0.0.1`; `AIDRIVEN_PORT`, default `8731`; `AIDRIVEN_RELOAD`; `AIDRIVEN_STORAGE_SECRET`; `AIDRIVEN_DATA_DIR`), read by `EnvSettings` (pydantic-settings, also reads `.env`). There are **no command-line flags**.
3. **Live updates by polling, not subscription**: long operations run as `asyncio` tasks owned by `RunnerService` (`LiveRun`); pages refresh with `ui.timer` (1 s on the run detail page, 3 s on the runs list and on Logs) reading the repository and the in-memory `LiveRun` (active items, last 300 events). No event bus exists.
4. Components: `ui.table` (Quasar tables with icon-button action slots), `ui.echart` (radar, grouped bars, cost-vs-quality scatter), `ui.codemirror` (YAML check editor, task import), `ui.code`, `ui.markdown`. `ui.aggrid` is not used.
5. **Dialogs are attached to the page root** (`common.root_dialog()` creates them inside `ui.context.client.content`) so they survive `@ui.refreshable` refreshes of the element that opened them.
6. Generated HTML is shown only in `<iframe sandbox="allow-scripts" referrerpolicy="no-referrer" srcdoc="…">` (`common.sandboxed_iframe`); never `allow-same-origin`. No CSP is injected into the document.
7. `nicegui` is imported only in `ui/` and `app.py`. Pages talk to the `AppContext` (repository, runner, check engine, sandbox, config) obtained with `common.ctx()`.

## Alternatives considered

- **Event bus + `subscribe()` per page** (ADR-001): lower latency, but more moving parts; 1 s polling is enough for a single-user local tool.
- **CLI flags** (`argparse`/`typer`): duplicated what pydantic-settings already provides via env/`.env`.

## Consequences

- (+) One process, pure Python, trivial to start; env vars and `.env.example` document every startup setting.
- (+) Polling makes pages robust to reloads and to runs started from another page.
- (−) Up to ~1 s latency in progress updates, and each open run page reads the run's results once per change.
- (−) The HTML preview relies only on the iframe sandbox (no CSP): scripts in the artifact may try network requests from the browser (they cannot read the app's origin, cookies or DOM).
