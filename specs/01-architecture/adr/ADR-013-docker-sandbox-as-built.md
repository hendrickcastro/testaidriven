# ADR-013 — Docker sandbox with subprocess fallback (as built)

- **Status**: Accepted — §2 browser image superseded by [ADR-020](ADR-020-browser-sandbox-image-built-locally.md); §5 output contract extended by [ADR-019](ADR-019-authenticated-check-results.md)
- **Date**: 2026-09-24
- **Supersedes**: [ADR-004](ADR-004-docker-sandbox-with-process-fallback.md)

## Context

ADR-004 described read-only `/in` and `/out` mounts with a `tmpfs /work`, 128 pids, `python -I` in the fallback, an injected CSP for HTML previews and a *Prepare images* action. The implementation chose a simpler mount layout (one bind-mounted work dir), pulls images lazily and uses the Playwright image's own user for browser jobs.

## Decision

1. `SandboxManager` (`adapters/sandbox.py`) selects the sandbox from `AppConfig.sandbox.mode`: `auto` (default: Docker if `ping()` answers — cached 15 s — else process), `docker` (a job fails with *"Docker is required … but not reachable"* if the daemon is down), `process`.
2. **`DockerSandbox`** — one container per job, force-removed afterwards:
   - `network_disabled=True`, `mem_limit` = `memswap_limit` = `memory_mb` (default 512 MiB, so no swap), `nano_cpus` = `cpus × 1e9` (default 1.0), `pids_limit` (default **256**), `read_only=True` root, `tmpfs /tmp` (`rw,size=128m`), `cap_drop=["ALL"]`, `security_opt=["no-new-privileges"]`, `HOME=/tmp`.
   - User **`65534:65534`** (nobody) for Python jobs; **`pwuser`** for browser jobs in the Playwright image (`ipc_mode="shareable"`; Python jobs use `private`).
   - The job files are written to a host temp dir (chmod 777) **bind-mounted read-write at `/work`** (the working directory); collected outputs are read back from it.
   - Timeout: `container.wait(timeout)`; on expiry the container is killed and `timed_out=True`.
   - Images: `python:3.12-slim` and `mcr.microsoft.com/playwright/python:v1.55.0-noble` (both editable in *Settings > Sandbox*). An image missing locally is **pulled on first use**, guarded by a lock so concurrent jobs pull it once.
3. **`ProcessSandbox`** (fallback) — temp dir as cwd, `sys.executable` for `python`, stdin closed, environment reduced to `PATH`, `PYTHONIOENCODING`, `PYTHONDONTWRITEBYTECODE`, `PYTHONNOUSERSITE`, `TEMP/TMP/HOME` = temp dir and, when present, `SYSTEMROOT`, `WINDIR`, `COMSPEC`, `PLAYWRIGHT_BROWSERS_PATH`, `LOCALAPPDATA`; new process group/session; timeout → process-tree kill (`taskkill /T /F` on Windows, `killpg` elsewhere). **No network or filesystem isolation**: the header badge turns orange ("process"), the dashboard shows a warning and every `ExecutionRecord` carries `isolation="process"`.
4. stdout/stderr are truncated to 200,000 characters; requested text files are collected; `_screenshot.png` (written by the harness' `open_page`) is returned as `screenshot_b64`.
5. The harness `aidriven_harness.py` is stdlib-only and copied into every job, so one check format works in both sandboxes; without Playwright, `open_page` prints `AIDRIVEN_SKIP` and the check is skipped (neither scored nor penalized).
6. HTML previews in the UI never run in the app process: sandboxed iframe as in [ADR-010](ADR-010-nicegui-ui-as-built.md).
7. *Settings > Sandbox > Test sandbox* runs a probe that prints the Python version and whether an outbound socket to `1.1.1.1:53` is blocked.

## Alternatives considered

- **`/in` read-only + `tmpfs /work` + `/out`** (ADR-004): stronger separation, but copying outputs out of a tmpfs needs `docker cp`/archives; a per-job temp dir that is deleted afterwards gives the same host protection.
- **Explicit *Prepare images* button**: replaced by lazy pull with a lock (first run is slower, nothing to remember).

## Consequences

- (+) Real isolation whenever Docker Desktop runs; the app keeps working without it.
- (−) The first job per image may take minutes (image pull, ~2 GB for Playwright).
- (−) The process fallback runs model code with the user's permissions; it is visible but not safe for adversarial code.
- (−) The Playwright image tag must match the Playwright version the checks expect; it is pinned by config, not verified.
