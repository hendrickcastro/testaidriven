# Module: Artifacts and sandbox

`services/artifacts.py` (extraction), the `ArtifactStorePort` adapters, the `SandboxPort` adapters (`SandboxManager`, `DockerSandbox`, `ProcessSandbox`), the harness and the *Artifacts* page. Extracts the files a model produces, stores them locally or in Firebase Storage, and **reproduces them in isolation** — running programs and HTML pages without touching the host ([P4](../00-overview/constitution.md), [ADR-013](../01-architecture/adr/ADR-013-docker-sandbox-as-built.md), [ADR-014](../01-architecture/adr/ADR-014-firebase-storage-as-built.md)).

## Output convention

When a task declares `artifact_names`, the runner appends the delivery instructions (`runner.artifact_instructions`, [../05-llm/prompts.md](../05-llm/prompts.md)) to the user message:

````
```python file=solver.py
def solve(grid): ...
```
````

Answer tasks ask in their own prompt for a last line `FINAL ANSWER: <value>`.

## Extraction (`extract_files(text, expected_names)`)

Fenced blocks (```` ``` ```` or `~~~`, 3+ chars) are scanned in order. The file name of a block is taken from, in this order:

1. The info string: `file=`, `filename=`, `title=` or `path=` (also with `:`; quotes allowed) — marked `explicit=True` (conformity signal).
2. A bare file name as the second token of the info string (```` ```python solver.py ````), or as the only token.
3. The line right before the block, if it is just a file name (optionally bold, backticked, a heading or `file:`), < 160 chars.
4. A first line inside the block like `# file: x.py`, `// file: x.js`, `<!-- file: x.html`, `-- file: x.sql`.

Rules: names are normalized to their basename (`../../evil.py` → `evil.py`); a later block with the same name wins (models often revise); **fallback** — if the task expects exactly one file and it was not found, the last unnamed block whose language maps to its extension (else the last unnamed block) is assigned that name. `kind` from the extension: `.py`→`python`, `.html`/`.htm`→`html`, `.js`/`.mjs`→`javascript`, `.json`→`json`, `.sql`→`sql`, `.md`→`markdown`, anything else→`text`. There are no size or count limits. Agent `write_file` files are artifacts too and win over fenced blocks with the same name ([rules-agents.md](rules-agents.md)).

## Storage

- The runner stores each file (UTF-8) through the store for new artifacts: local `data/artifacts/<run_id>/<result_id>/<name>` or, with *Save artifacts in the cloud* on, `aidriven/<run_id>/<result_id>/<name>` in the bucket. Path segments pass through `safe_name` (flatten, `[A-Za-z0-9._-]`, ≤ 150 chars).
- Metadata (`name`, `kind`, `size`, `sha256`, `storage`, `location`, `mime`) goes to `aidriven_artifacts`; `Result.artifact_ids` lists them. A store failure is logged and that artifact is skipped.
- **Firebase Storage prerequisite**: the `algoritxia` project has no bucket yet (verified 2026-09-24: `algoritxia.firebasestorage.app` and `algoritxia.appspot.com` → 404). Enable Storage in the Firebase console before turning the toggle on ([persistence-firestore.md](persistence-firestore.md#firebase-storage-settings--storage)).

## Sandbox execution (`SandboxJob` → `ExecutionRecord`)

`SandboxJob(files: dict[str, bytes], command: list[str], timeout_s=60, needs_browser=False, collect=[])`. For a check the job files are: the result's artifacts, `check.files`, `response.txt` (full response), `aidriven_harness.py`, `_check.py` and the single-use nonce file `_aidriven_nonce` ([ADR-019](../01-architecture/adr/ADR-019-authenticated-check-results.md)); command `python _check.py` (`python` resolves to `python3` in Docker and to `sys.executable` in the fallback).

| Setting | Docker (`DockerSandbox`) | Process fallback (`ProcessSandbox`) |
|---|---|---|
| Network | `network_disabled=True` | not isolated (header badge "process", dashboard warning) |
| Filesystem | read-only root; job dir bind-mounted rw at `/work` (cwd); `tmpfs /tmp` 128 MiB; job dir deleted afterwards | temp dir as cwd, deleted afterwards |
| Limits | `memory_mb` (512, swap disabled), `cpus` (1.0), `pids_limit` (256) — *Settings > Sandbox* | timeout only |
| User | `65534:65534`; `pwuser` for browser jobs; `cap_drop ALL`, `no-new-privileges` | current user; scrubbed environment; new process group |
| Shared memory | 64 MiB; **256 MiB for browser jobs** (Chromium) | — |
| Timeout | `container.wait(timeout)` then `kill()` → `timed_out=True` | `communicate(timeout)` then process-tree kill |
| Image | `python:3.12-slim` (pulled on first use); for `needs_browser` jobs **`aidriven-browser:1.63.0`**, built locally on first use from `mcr.microsoft.com/playwright/python:v1.63.0-noble` + `pip install playwright==1.63.0` ([ADR-020](../01-architecture/adr/ADR-020-browser-sandbox-image-built-locally.md)) | host Python; browser checks need the optional extra `html` (`pip install -e ".[html]"` + `python -m playwright install chromium`) and are skipped without it |

`SandboxManager` picks the sandbox per job from `sandbox.mode` (`auto`/`docker`/`process`). The official Playwright image ships browsers but not the Python package (verified 2026-09-24), hence the local build; the base image is `SandboxConfig.browser_base_image` and the built tag `SandboxConfig.playwright_image` (the build is triggered for tags starting with `aidriven-browser:`; other names are pulled). Output: stdout/stderr truncated to 200,000 chars; files listed in `collect` returned as text (`.png` → `screenshot_b64`); `_screenshot.png` is returned whenever present.

## Harness (`aidriven_harness.py`, stdlib only)

On import the harness reads **and deletes** `_aidriven_nonce` (before any model code is loaded) and keeps it only inside the emitter closure (`_make_emitter`); result and skip lines are written with `os.write(1, …)` as `AIDRIVEN_RESULT <nonce> {json}` / `AIDRIVEN_SKIP <nonce> {json}`, bypassing `print`/`sys.stdout` ([ADR-019](../01-architecture/adr/ADR-019-authenticated-check-results.md)).

`case(name, weight=1.0, timeout=10.0)` registers a test (run on a watched thread; `timeout=0` runs on the main thread, required by sync Playwright) · `report()` runs the cases and emits `AIDRIVEN_RESULT <nonce> {"passed", "total", "score" (passed weight / total weight), "failures"[≤30]}` · `response_text()`, `final_answer()` (last `FINAL ANSWER:` line) · `read(name)`, `exists(name)` · `load_module(filename)` (raises if the model did not deliver it) · `skip(reason)` emits `AIDRIVEN_SKIP <nonce> {"reason"}` and exits 0 · `open_page(filename, width, height)` context manager: headless Chromium with every `http(s)` request aborted, collects `pageerror`s, writes `_screenshot.png` on exit; skips when Playwright is not importable.

## Reproduction from the UI

Available on the *Artifacts* page (run selector → one expansion per artifact: `task · model #rep — name (size · storage)`) and inside the result page:

- **Download** (any kind).
- **Python**: *Run in sandbox* executes `python <name>` with all artifacts of the same result, `timeout = sandbox.default_timeout_s`; shows exit code, duration, isolation, timeout flag, stdout and stderr (with a warning when isolation is `process`). No stdin/arguments.
- **HTML**: tabs *Preview* (`<iframe sandbox="allow-scripts" referrerpolicy="no-referrer" srcdoc>`, never `allow-same-origin`) and *Source*; *Render headless* runs `open_page` in the Playwright image and shows the screenshot plus page errors and visible text.
- Other kinds: code viewer with highlighting (first 300,000 chars).

## Business rules

- No artifact or check code is ever executed in the app process; checks and replays always go through `SandboxManager`.
- The process fallback is used when `sandbox.mode` is `process`, or `auto` with Docker unavailable; with mode `docker` and the daemon down, jobs fail with an explicit error instead of falling back, and the header shows a red **"Sandbox: Docker down"** badge.
- Only result/skip lines carrying the job's nonce count; forged lines are ignored and reported, two authenticated lines mean tampering (score 0) — see [evaluation.md](evaluation.md).
- Every `ExecutionRecord` records its `isolation`; sandbox check details end with `(docker)` or `(process)`.
- Checks of one result run sequentially; different results run in parallel up to the run's concurrency.
- The harness stays stdlib-only so the same checks work in both sandboxes.

## Acceptance criteria

- [x] A response with two named blocks yields two artifacts with correct names and `kind` (`test_artifacts.py::test_explicit_file_convention`).
- [x] File names on the previous line, in a first-line comment and via the single-file fallback are recognised (`test_artifacts.py`).
- [x] A name like `../../evil.py` is flattened to `evil.py`, and the local store refuses locations that escape its root (`test_artifacts.py`, `test_infra.py::test_local_store_rejects_escape`).
- [x] An infinite loop is killed at the timeout and reported (`test_checks.py::test_timeout_is_reported`).
- [x] A run stores the oracle's artifact and its metadata (`test_runner.py::test_full_run_with_oracle_and_echo`).
- [x] In Docker, network is blocked, the root filesystem is read-only, the user is not root and work-dir outputs are collected (`tests/integration/test_docker_sandbox.py::test_no_network_readonly_root_and_workdir_output`, marker `docker`).
- [x] In Docker, a sleeping job is killed at the timeout (`test_docker_sandbox.py::test_timeout_kills_container`).
- [x] In Docker, an HTML page renders in headless Chromium with its script executed and network disabled (`test_docker_sandbox.py::test_html_page_in_headless_browser`).
- [x] An artifact printing a fake result line, or trying to read the nonce, cannot change the score (`test_checks.py::test_forged_result_line_from_artifact_is_ignored`, `test_artifact_cannot_read_the_nonce`).
- [ ] The HTML preview cannot read the app's cookies/DOM (iframe without `allow-same-origin`, probe page).
- [ ] With Docker stopped and mode `auto`, checks run in the process fallback and the header badge shows "process"; with mode `docker` it shows "Sandbox: Docker down".
- [ ] With the bucket enabled and cloud artifacts on, new artifacts go to `aidriven/…` and old local ones remain viewable.
