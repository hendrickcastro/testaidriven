# ADR-020 — Browser sandbox image built locally (`aidriven-browser:<version>`)

- **Status**: Accepted
- **Date**: 2026-09-24
- **Supersedes**: the image choice for browser jobs in [ADR-013](ADR-013-docker-sandbox-as-built.md) §2 (the rest of ADR-013 stands)

## Context

ADR-013 ran browser jobs (`html_playwright` checks, agent `run_html`, *Render headless*) directly in `mcr.microsoft.com/playwright/python:v1.55.0-noble`. Verified on 2026-09-24: the official `mcr.microsoft.com/playwright/python` image ships the browsers but **not** the Python `playwright` package, so `from playwright.sync_api import …` fails and every browser check was skipped. The image tag was also not tied to any Playwright version declared by the project.

## Decision

1. `SandboxConfig.playwright_image` defaults to **`aidriven-browser:1.63.0`**, a local image; a new field `SandboxConfig.browser_base_image` defaults to **`mcr.microsoft.com/playwright/python:v1.63.0-noble`**.
2. On first use, if the configured image is missing and its name starts with `aidriven-browser:`, `DockerSandbox` **builds it** (under the same lock as pulls) from an inline Dockerfile: `FROM <browser_base_image>` + `pip install playwright==<tag version>` (with `--break-system-packages` fallback). The tag is the Playwright version, so browsers (base image) and Python package always match. Any other image name is pulled as before.
3. Browser jobs run with `shm_size=256m` (Chromium crashes with Docker's 64 MB default), user `pwuser`, and the same isolation as Python jobs (no network, read-only root, `tmpfs /tmp`, dropped capabilities, limits).
4. For browser checks in the **process** sandbox, `pyproject.toml` offers the optional extra `html = ["playwright>=1.50"]` plus `python -m playwright install chromium`. Playwright 1.63 is installed in the project venv as of 2026-09-24.
5. Verified with `tests/integration/test_docker_sandbox.py` (marker `docker`, skipped without a daemon): no network + read-only root + non-root user + work-dir output; timeout kill; an HTML page rendered in headless Chromium (script ran) with network disabled.

## Alternatives considered

- **Keep the official image and install Playwright at job start**: needs network inside the sandbox — forbidden.
- **Publish our own image to a registry**: extra infrastructure for a single-user tool; a local build from a pinned base is reproducible enough.

## Consequences

- (+) Browser checks actually run under Docker, with Python package and browsers at the same pinned version.
- (−) The first browser job builds the image (pull of the ~2 GB base + pip install; needs network on the host, not in the sandbox).
- (−) Upgrading Playwright means changing both defaults (`aidriven-browser:<v>` and the base tag `v<v>-noble`) together.
