# ADR-004 — Docker sandbox with local subprocess fallback

- **Status**: Superseded by [ADR-013](ADR-013-docker-sandbox-as-built.md) (2026-09-24)
- **Date**: 2026-09-24

## Context

Artifacts must be reproducible "in a controlled environment, e.g. running a program or an HTML page, embedding that execution so it does not alter the host". Checks such as `python_tests`, `python_verifier`, `sql_result` and `html_playwright` run **model-generated code**, and agents run tools (`run_python`, `run_html`) in a loop. Docker Desktop is installed on the user's Windows machine but is often stopped.

## Decision

1. `SandboxPort` with two adapters, selected by `aidriven_settings/sandbox.backend` = `auto` (default: Docker if the daemon answers, else process) | `docker` | `process`.
2. **`DockerSandbox`** — one container per execution, removed afterwards:
   - `network_mode="none"`, `mem_limit` (512 MiB), `nano_cpus` (1 CPU), `pids_limit` (128), `read_only=True`, `cap_drop=["ALL"]`, `security_opt=["no-new-privileges"]`, user `65534:65534` (nobody).
   - Inputs bind-mounted read-only at `/in`; working dir is a `tmpfs` at `/work` (exec, 64 MiB) plus `tmpfs /tmp`; declared outputs copied to a small bind-mounted `/out`.
   - Wall-clock timeout enforced by the host (`container.kill()`), `timed_out=True`.
   - Images: `python:3.12-slim` (programs, checks) and `mcr.microsoft.com/playwright/python` (HTML, headless Chromium with network routes aborted by the harness).
3. **`LocalProcessSandbox`** (fallback) — temp directory, `python -I` (isolated mode), environment cleared except the minimum (`PATH`, `SYSTEMROOT`, `TEMP`), cwd = temp dir, timeout with process-tree kill. It **does not isolate network or filesystem**: the UI shows a permanent warning banner and every `ExecutionRecord` carries `isolation="process"`.
4. The harness (`aidriven_harness.py`) is stdlib-only, so the same check script works in both adapters; missing Playwright → `AIDRIVEN_SKIP` (check skipped, neither scored nor penalized).
5. HTML preview in the UI never executes on the host process: `<iframe sandbox="allow-scripts">` with `srcdoc` and an injected CSP that blocks network.

## Alternatives considered

- **Docker only**: strongest guarantee, but the app would be unusable whenever Docker Desktop is stopped.
- **WASM (Pyodide) or RestrictedPython**: no daemon, but incomplete stdlib/performance for hard algorithmic tasks and weaker guarantees than a container.
- **Windows Sandbox / VMs**: strong isolation, far too slow per check.

## Consequences

- (+) Real isolation by default when Docker runs; the app still works without it.
- (+) One harness, one check format for both backends.
- (−) The fallback is less safe; mitigated by the warning, the clean environment and the fact that the user can force `docker`.
- (−) Container start adds ~0.5–2 s per check; checks of one result run sequentially in one container when possible.
