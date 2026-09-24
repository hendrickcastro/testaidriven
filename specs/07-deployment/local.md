# Deployment — local (Windows)

AIDriven is a single-user local application: no server deployment, no container for the app itself. Docker is optional and only used as the sandbox ([ADR-013](../01-architecture/adr/ADR-013-docker-sandbox-as-built.md)).

## Requirements

| Requirement | Version | Notes |
|---|---|---|
| Python | ≥ 3.12 (developed on 3.14; CI on 3.12 and 3.14) | `py --list` shows installed versions |
| Git | any | |
| Docker Desktop | optional | Enables the isolated sandbox; without it the process fallback is used (orange header badge) |
| Browser | any modern | The UI is served at `http://127.0.0.1:8731` (not opened automatically) |

## Helper script `run.sh`

`./run.sh` (Git Bash on Windows, or any bash on Linux/macOS) shows a numbered menu; `./run.sh <number|name> [extra args]` runs an option directly (extra args are forwarded, e.g. `./run.sh test -k runner`; `./run.sh help` lists them):

| # | Name | Action |
|---|---|---|
| 1 | `create-env` | Create `.venv` with the newest Python ≥ 3.12 found, install `.[dev]`, copy `.env.example` → `.env` |
| 2 | `install` | Install / update dependencies |
| 3 | `browser` | Install Playwright + Chromium (browser checks without Docker) |
| 4 | `run` | Run the app |
| 5 | `dev` | Dev mode with auto-reload — launches the **script form** (`python src/aidriven/__main__.py`), because NiceGUI cannot auto-reload under `python -m`; with `AIDRIVEN_RELOAD=true` and `python -m aidriven` the app falls back to no reload and prints a warning |
| 6 | `open` | Open the app in the browser |
| 7 | `stop` | Free the port: kills the app listener and the dev-mode reloader parent only |
| 8–10 | `test`, `test-fast`, `test-all` | Unit tests; fast unit tests (skip seed validation); all tests incl. Docker integration |
| 11 | `images` | Build / pull the Docker sandbox images |
| 12 | `docker` | Docker status |
| 13 | `quality` | Format + lint + type-check |
| 14 | `coverage` | Tests with coverage report |
| 15 | `backup` | Back up the local database |
| 16 | `reset` | Reset local data (asks for confirmation) |
| 17 | `assets` | Regenerate seed assets (`tools/gen_seed_assets.py`) |
| 18 | `logs` | Follow the application log |
| 19 | `info` | Environment info |

The manual equivalents follow.

## Setup (PowerShell)

```powershell
cd D:\_ALGORITXIA\testaidriven
py -3.14 -m venv .venv            # or: py -3.13 / py -3.12
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
pre-commit install                # optional: ruff, ruff-format, yaml/json checks, detect-private-key, mypy
copy .env.example .env            # optional: AIDRIVEN_* startup values, AIDRIVEN_ENCRYPTION_KEY, provider key fallbacks
```

## Run

```powershell
python -m aidriven                # or the console script: aidriven
$env:AIDRIVEN_PORT = "8732"; python -m aidriven     # startup options are env vars (or .env), there are no CLI flags
```

Start it **from the repository root**: the fake `oracle` model reads reference solutions from `tests/fixtures/seed_solutions/` relative to the working directory.

The first start creates `data/` (SQLite database and logs; `data/config.json` is written on the first settings change), syncs the seed tasks and bootstraps an **offline demo**: provider *Fake (offline demo)* with profiles *Fake oracle*, *Fake echo*, *Fake judge*, *Fake tools*; rulesets *Rigor* and *Concise output*; agent *Python engineer (sandbox)*; suite *Hard battery (seed)* (seed tasks × oracle + echo). Then, in the UI:

1. *Models* → open a provider card, paste the API key (Windows Credential Manager on SQLite; encrypted in Firestore when active), *Save* / *Test*, then *Add* recommended models or pick from *All models from the provider*.
2. *Settings > Judge* (or *Use as judge* in *Models*) → choose the judge profile.
3. (Optional) *Settings > Firestore* → upload the service-account JSON → *Test connection* → *Activate* → *Migrate local → Firestore*.
4. (Optional) *Settings > Storage* → only after **Storage is enabled in the Firebase console** (see below) → set the bucket → *Test storage* → switch on.
5. (Optional) *Settings > Sandbox* → start Docker Desktop → *Test sandbox* (the first Docker job pulls `python:3.12-slim`; the first browser job **builds** `aidriven-browser:1.63.0` from the ~2 GB `mcr.microsoft.com/playwright/python:v1.63.0-noble` base plus `pip install playwright==1.63.0` — the official image does not include the Python package).

## Verified environment facts (2026-09-24)

- Firestore activation on `algoritxia`: the bootstrap created the demo data and the 14 seed tasks (after the nested-array fix); API keys are stored encrypted — only ciphertext at rest, decryption from a fresh cache works, cleanup OK.

- Firestore, project **`algoritxia`**: service-account upload and *Test connection* succeeded ("write/read/delete OK on aidriven_health"); the app still runs on SQLite until *Activate*.
- Firebase Storage, project **`algoritxia`**: **no bucket exists yet** — `algoritxia.firebasestorage.app` and `algoritxia.appspot.com` both return 404. The app pre-fills `algoritxia.firebasestorage.app`; enable Storage in the Firebase console (Build > Storage > Get started) before turning *Save artifacts in the cloud* on, otherwise cloud artifact saves fail.
- Host: Playwright 1.63 is installed in the project venv (`pip install -e ".[html]"` + `python -m playwright install chromium`), so browser checks also run under the process fallback.
- Docker: `tests/integration/test_docker_sandbox.py` passes — no network, read-only root, non-root user, timeout kill, and an HTML page rendered in headless Chromium in `aidriven-browser:1.63.0`.

## Quality checks

```powershell
ruff check src tests ; ruff format --check src tests
mypy src
pytest --cov=aidriven -m "not docker and not firestore"   # what CI runs (.github/workflows/ci.yml)
pytest -m docker                                          # tests/integration/test_docker_sandbox.py (skipped without Docker)
pytest -m firestore                                       # marker declared; no Firestore tests yet
```

CI sets `PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring`, so tests never touch the real keyring.

## Where data lives

| What | Location | In git |
|---|---|---|
| User configuration (language, Firestore, storage, judge, sandbox) | `data/config.json` | no (`data/` ignored) |
| Local database | `data/aidriven.db` (+ `-wal`, `-shm`) | no |
| Database backups | `data/backups/aidriven-<YYYYmmdd-HHMMSS>.db` | no |
| Artifacts (local store) | `data/artifacts/<run>/<result>/<name>` | no |
| Firestore service account | `data/secrets/firestore-sa.json` | no |
| Logs | `data/logs/aidriven.log*` (index hints are in memory only) | no |
| API keys | Windows Credential Manager, service `aidriven`, keys `provider:<id>:api_key`; with Firestore active also **encrypted** in `aidriven_settings/secret__provider:<id>:api_key` (keyring = local cache) | no |
| Key-encryption material | `AIDRIVEN_ENCRYPTION_KEY` (`.env`) or, by default, the uploaded service account's `private_key` | no |
| Seed tasks and assets | `src/aidriven/seed/` (assets regenerated by `tools/gen_seed_assets.py`) | yes |
| Seed reference solutions | `tests/fixtures/seed_solutions/<slug>/` | yes |
| Shared Firebase config | `config/firebase.json`, `config/firestore.rules`, `config/firestore.indexes.json` (not modified by this project) | as-is |
| Existing service-account key | `config/algoritxia.json` | **never** (git-ignored) |
| NiceGUI per-browser storage | `.nicegui/` | no (git-ignored) |

## Backup and restore

- **SQLite**: *Settings > Local data > Backup* (file copy into `data/backups/`), or stop the app and copy `data/aidriven.db` and `data/artifacts/`. Restore = copy them back with the app stopped.
- **Tasks**: *Tasks > Export YAML* (all tasks when none is selected) is the portable backup of the battery.
- **Firestore**: data stays in the `algoritxia` project; for a snapshot use a managed export limited to `aidriven_*` collections (`gcloud firestore export gs://<bucket>/aidriven-backup --collection-ids=aidriven_runs,aidriven_results,...`). There is no Firestore → local migration in the UI.
- **Configuration** is not in any data backup: keep a copy of `data/config.json`. **Keys**: on SQLite they live only in the keyring (re-enter them or use env vars on a new machine); with Firestore active they travel encrypted with the data and decrypt on any machine with the same service account or `AIDRIVEN_ENCRYPTION_KEY` ([ADR-022](../01-architecture/adr/ADR-022-encrypted-api-keys-in-firestore.md)). Rotating the service account without `AIDRIVEN_ENCRYPTION_KEY` makes the stored keys undecryptable (re-enter them).

## Troubleshooting

- *Docker unavailable*: start Docker Desktop; the header badge switches from "process" to "docker" within ~15 s (availability is cached). With mode `docker`, jobs fail explicitly instead of falling back.
- *Firestore "index required"*: open *Logs*, copy the URL and create the index in the console; record it in [../02-data-model/firestore-collections.md](../02-data-model/firestore-collections.md).
- *Firestore enabled but unreachable at startup*: the app logs an ERROR and runs on SQLite; fix credentials in *Settings > Firestore*.
- *No keyring backend* (non-Windows headless): keys live in memory only; set the provider env vars (source "env").
- *Port in use*: `$env:AIDRIVEN_PORT = "8732"`.
- *A run was interrupted by a crash*: at the next start it is marked `partial` (its in-flight results back to `pending`); open it and click *Resume*.

## Checklist

- [x] `pytest -m "not docker and not firestore"` passes without Docker or Firestore (81 tests collected on 2026-09-24 incl. 3 Docker integration tests; the seed reference tests take ~70 s).
- [ ] `pip install -e ".[dev]"` succeeds in a fresh venv and the CI workflow is green on the remote.
- [ ] `python -m aidriven` opens the Dashboard and creates `data/aidriven.db`.
- [x] With Docker Desktop running, `pytest -m docker` passes (isolation, timeout, headless browser).
- [ ] With Docker Desktop running, *Test sandbox* reports `docker` and `network: blocked`.
- [ ] Firebase Storage enabled for `algoritxia` and *Test storage* passing.
