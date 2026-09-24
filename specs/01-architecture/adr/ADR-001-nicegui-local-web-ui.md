# ADR-001 — NiceGUI as a local web UI

- **Status**: Superseded by [ADR-010](ADR-010-nicegui-ui-as-built.md) (2026-09-24)
- **Date**: 2026-09-24

## Context

The request asks for "a Python program with a UI". The UI must show long-running runs with live progress, dense tables and charts (radar, bars), forms with many conditional fields (model capabilities), a code/artifact viewer and an **isolated preview of generated HTML**. The whole project is Python and single-user; we want no JavaScript build toolchain and no separate backend/frontend deployment.

## Decision

1. Use **NiceGUI** (≥ 3) as the UI framework: a local web app started with `python -m aidriven`, bound to `127.0.0.1:8731` by default (configurable with `--host/--port` or `AIDRIVEN_HOST/AIDRIVEN_PORT`).
2. The UI runs in the same asyncio loop as the services; long operations (runs) are background tasks and the UI subscribes to progress events — it never blocks the event loop (sync SDKs go through `asyncio.to_thread`).
3. Charts with `ui.echart` (radar, bars, box plots); tables with `ui.table`/`ui.aggrid`; code with `ui.code`/`ui.codemirror`.
4. Generated HTML is shown only in `<iframe sandbox="allow-scripts" srcdoc="...">` ([P4](../../00-overview/constitution.md)).
5. `nicegui` is imported only in `ui/` and `app.py`; pages call services, never adapters.

## Alternatives considered

- **Streamlit**: very fast to prototype but its rerun-the-script model fits poorly with long async runs, live progress and complex stateful forms.
- **Desktop GUI (PySide6/Qt, Tkinter)**: no browser needed, but embedding an isolated HTML preview and rich charts is heavier, and packaging Qt is costly.
- **FastAPI + React SPA**: maximum control, but two stacks, a JS build and a REST contract to maintain for a single-user local tool.
- **Gradio**: aimed at model demos, not at CRUD + dashboards.

## Consequences

- (+) Pure Python end to end; live updates over WebSocket for free.
- (+) The browser gives a proper sandboxed iframe for HTML artifacts.
- (−) Tied to NiceGUI's component set; mitigated because the UI is a thin layer over services (a new UI would only replace `ui/`).
- (−) A local web server is exposed; mitigated by binding to `127.0.0.1` by default and never serving secrets.
