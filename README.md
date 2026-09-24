# AIDriven (testaidriven)

Local test bench for LLMs. It runs a configurable battery of very hard tasks against any number of model profiles — each tuned with everything the model supports (reasoning effort, thinking budget or level, verbosity, temperature, seed, vision, tools, JSON mode) — and measures **reliability, performance, conformity and intelligence**. Every call records tokens (input, output, reasoning, cached), latency, time to first token, tokens/s, cost, retries and refusals. Automatic checks score what can be verified, a configurable **judge model** gives a blind 1–10 preview, and **you** have the final score. Artifacts produced by the models (programs, HTML pages) are reproduced in an isolated sandbox (Docker, with a subprocess fallback) without touching the host. Results can be compared per task and overall, between runs, models and re-runs. Data lives in local SQLite until you activate Firestore (collections prefixed `aidriven_`); artifacts are stored locally or, optionally, in Firebase Storage. Python + NiceGUI, UI in English and Spanish. The original request is in [_Documentation/InitializeProject.md](_Documentation/InitializeProject.md).

## Project structure

```
testaidriven/
├── _Documentation/      # Original request (immutable)
├── README.md            # This file
├── pyproject.toml       # Package "aidriven" (hatchling), console script "aidriven", dev extras, ruff/mypy/pytest config
├── .env.example         # AIDRIVEN_* startup settings and provider key fallbacks
├── .pre-commit-config.yaml / .github/workflows/ci.yml   # ruff, mypy, pytest (CI on Python 3.12 and 3.14)
├── config/              # Shared Firebase project config (algoritxia) — rules/indexes are NOT modified here
├── specs/               # Full specification (source of truth, updated to the implemented behaviour)
│   ├── 00-overview/     # Vision, constitution (P1..P10), glossary
│   ├── 01-architecture/ # Hexagonal architecture, stack, ADRs (ADR-010..023 current, 001..009 superseded)
│   ├── 02-data-model/   # Collections (prefix aidriven_), data/config.json, queries/indexes
│   ├── 03-modules/      # Models, tasks, rules/agents, battery, execution, evaluation, artifacts/sandbox,
│   │                    # comparison, persistence/Firestore, logs
│   ├── 04-api/          # Internal service and port contracts
│   ├── 05-llm/          # LLM port, per-provider parameter mapping, prompts
│   ├── 06-ui/           # NiceGUI pages, routes and i18n
│   ├── 07-deployment/   # Local setup on Windows, verified environment facts, data locations, backup
│   └── 08-roadmap/      # Phases F0..F7 (implemented), test inventory, next steps
├── src/aidriven/        # domain/ ports.py adapters/ services/ ui/ seed/ sandbox_harness/ config.py context.py app.py
├── tests/               # unit/ (offline: fake provider, SQLite in tmp, process sandbox) · integration/ (Docker) · fixtures/seed_solutions/
├── run.sh               # Helper menu: env, install, run/dev/stop, tests, images, quality, backup, reset, logs…
├── tools/               # gen_seed_assets.py (seed assets) · gen_locales.py (source of truth for UI strings)
└── data/                # Created at runtime (git-ignored): config.json, SQLite, backups, artifacts, secrets, logs
```

## Quick start

The helper script `run.sh` (Git Bash on Windows, or any bash) shows a numbered menu with every task — create the
environment, run the app, tests, quality checks, sandbox images, backup/reset, logs:

```bash
./run.sh            # interactive menu
./run.sh 1          # create environment (.venv + dependencies + .env)
./run.sh run        # start the app → http://127.0.0.1:8731   (./run.sh stop to free the port)
./run.sh help       # list options; extra args are forwarded, e.g. ./run.sh test -k runner
```

Manual equivalent:

```powershell
py -3.14 -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m aidriven                # from the repository root → http://127.0.0.1:8731
```

Startup options are environment variables (`AIDRIVEN_HOST`, `AIDRIVEN_PORT`, `AIDRIVEN_DATA_DIR`, … — see `.env.example`); there are no CLI flags.

The first start creates an **offline demo** (fake provider with *oracle*, *echo*, *judge* and *tools* models, two rulesets, a sandbox agent and the suite *Hard battery (seed)*), so you can run a full battery at zero cost from *Batteries*. For real models: open a provider card in *Models*, paste the key and *Add* recommended models (keys go to the Windows Credential Manager, or encrypted to Firestore when it is active), pick a judge in *Settings > Judge* (or *Use as judge*), and run. Details in [specs/07-deployment/local.md](specs/07-deployment/local.md).

Quality checks: `ruff check src tests`, `mypy src`, `pytest -m "not docker and not firestore"` (and `pytest -m docker` with Docker Desktop running). For browser checks without Docker: `pip install -e ".[html]"` + `python -m playwright install chromium`.

## How to use this specification

1. **Read first**: [specs/00-overview/vision.md](specs/00-overview/vision.md) and [specs/00-overview/constitution.md](specs/00-overview/constitution.md).
2. **To change behaviour**: update the module spec in `specs/03-modules/` (each closes with its acceptance criteria; `[x]` marks those covered by a test) and then the code; the implementation status and next steps are in [specs/08-roadmap/phases.md](specs/08-roadmap/phases.md).
3. **To decide**: every architectural decision is recorded as an ADR in [specs/01-architecture/adr/](specs/01-architecture/adr/). If something changes, a new ADR supersedes the old one — history is never edited, the old ADR only gets "Superseded by".
4. **Terms**: use the names of [specs/00-overview/glossary.md](specs/00-overview/glossary.md) exactly; they match the classes in `src/aidriven/domain/models.py`.

## Project invariants

- **Firestore prefix**: every collection starts with `aidriven_` (blobs with `aidriven/`). The shared `config/firestore.rules` and `config/firestore.indexes.json` are not modified or deployed by this project.
- **Replaceable technology**: the domain imports no SDK; providers, persistence, artifact storage, sandbox and secrets sit behind ports wired by `AppContext`. See [ADR-011](specs/01-architecture/adr/ADR-011-ports-and-adapters-as-built.md).
- **Generated code never runs on the host**: Docker sandbox (network off, limits, read-only root, non-root user; browser image `aidriven-browser:<playwright version>` built locally) or the subprocess fallback with a visible "process" badge; HTML previews only in `<iframe sandbox="allow-scripts">`. See [ADR-013](specs/01-architecture/adr/ADR-013-docker-sandbox-as-built.md).
- **Reproducible runs**: every run freezes a snapshot of tasks, profiles, rules, agent and judge; re-runs reuse it unless you choose current versions. See [ADR-017](specs/01-architecture/adr/ADR-017-run-snapshot-and-rerun-as-built.md).
- **The user has the last word**: judge = blind preview, user score prevails. See [ADR-015](specs/01-architecture/adr/ADR-015-judge-and-review-as-built.md).
- **English codebase, bilingual UI** (English default, Spanish available). See [ADR-018](specs/01-architecture/adr/ADR-018-ui-i18n-as-built.md).

## Current limitations

- Firebase Storage: project `algoritxia` has no Storage bucket yet (verified 2026-09-24) — enable it in the Firebase console before turning on *Save artifacts in the cloud*.
- Check results are authenticated with a single-use nonce ([ADR-019](specs/01-architecture/adr/ADR-019-authenticated-check-results.md)); a model that deliberately targets the harness internals could still forge a result — the checks and the candidate code share one sandbox process.
- The seed battery has 14 tasks; see [specs/03-modules/tasks.md](specs/03-modules/tasks.md#seed-battery-srcaidrivenseedtasksyaml).

## Security

API keys are stored in the OS keyring (Windows Credential Manager) on SQLite and, while Firestore is active, **encrypted** in Firestore (Fernet; key derived from `AIDRIVEN_ENCRYPTION_KEY` or the service account's private key — [ADR-022](specs/01-architecture/adr/ADR-022-encrypted-api-keys-in-firestore.md)). They are never stored in plaintext in the database, Firestore, `data/config.json` or exports, and every log handler redacts key-like strings and private keys. `config/algoritxia.json` and any uploaded service-account file (`data/secrets/`) are private keys with full access to the Firebase project: **never commit them** — they are covered by [.gitignore](.gitignore) and pre-commit's `detect-private-key`.
