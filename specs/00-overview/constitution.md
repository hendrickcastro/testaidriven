# Project constitution

Non-negotiable principles. Every design decision, ADR or line of code must respect them. If a change violates one, this document is amended explicitly first (see [Amendments](#amendments)).

## P1 — `aidriven_` prefix in Firestore

Every collection this project uses carries the prefix **`aidriven_`** (`config.COLLECTION_PREFIX`). The Firebase project `algoritxia` is shared with other applications (`comunio_*`, `llmchat_*`, `ContextAdmin_*`...). Storage blobs use the prefix `aidriven/` (`config.BLOB_PREFIX`, editable in *Settings > Storage*). No writes outside the prefix, ever. The shared `config/firestore.rules` and `config/firestore.indexes.json` belong to the whole Firebase project: this project **does not modify or deploy them** (see [../02-data-model/firestore-collections.md](../02-data-model/firestore-collections.md)).

## P2 — Replaceable technology (ports and adapters)

The pieces most likely to change are isolated behind the ports of `src/aidriven/ports.py` ([ADR-011](../01-architecture/adr/ADR-011-ports-and-adapters-as-built.md)):

| Component        | Port                | Default adapter                                   | Alternatives implemented                          |
|------------------|---------------------|---------------------------------------------------|---------------------------------------------------|
| LLM provider     | `LLMPort`           | one adapter class per `ProviderKind` family (6 classes for 8 kinds) | any new provider = one new adapter |
| Persistence      | `RepositoryPort`    | `SqliteRepository` (`data/aidriven.db`)           | `FirestoreRepository` (`aidriven_*`)              |
| Artifact storage | `ArtifactStorePort` | `LocalArtifactStore` (`data/artifacts/`)          | `FirebaseStorageArtifactStore` (`aidriven/`)      |
| Code execution   | `SandboxPort`       | `DockerSandbox` (via `SandboxManager`, mode `auto`) | `ProcessSandbox` (subprocess fallback)          |
| Secrets          | `SecretStorePort`   | `KeyringSecretStore` (Windows Credential Manager) on SQLite | `EncryptedRepoSecretStore` (Fernet ciphertext in `aidriven_settings`, keyring as cache) while Firestore is active; `MemorySecretStore` (no keyring backend / tests); env vars as read-only fallback |

Hard rule: **the domain does not import SDKs**. `domain/models.py` imports only pydantic. `anthropic`, `openai`, `google-genai`, `firebase-admin`, `docker`, `keyring` and `httpx` are imported only in `adapters/` (plus one lazy `firebase_admin` import in the composition root to dispose of the Firebase app when credentials change), and `nicegui` only in `ui/` and `app.py`. `context.py` (`AppContext`) is the only place that wires adapters together.

## P3 — Spec before code

`specs/` is the source of truth. A behaviour change is specified first (spec + ADR if it is an architectural decision) and implemented afterwards. ADRs are never edited to change history: a new ADR supersedes the old one, and the old one only gets its status line changed to "Superseded by ADR-0NN".

## P4 — Generated code never touches the host

Anything produced by an evaluated model (programs, HTML, scripts) and every check that executes it runs **only** through the `SandboxPort`: Docker with network disabled, resource limits, read-only root and a non-root user, or the subprocess fallback, which is signalled by an orange "process" badge in the header and a dashboard warning. HTML is previewed in the UI only inside `<iframe sandbox="allow-scripts">` (never `allow-same-origin`). See [ADR-013](../01-architecture/adr/ADR-013-docker-sandbox-as-built.md).

## P5 — Reproducibility

Every `Run` stores a `RunSnapshot`: frozen copies of the tasks, model profiles, rulesets, agent and judge it used. Results are always interpreted against that snapshot, never against the current version of an entity. A re-run reuses the original snapshot unless the user explicitly chooses current versions. See [ADR-017](../01-architecture/adr/ADR-017-run-snapshot-and-rerun-as-built.md).

## P6 — Secrets never in plaintext in the data store

API keys live in the `SecretStorePort`: the OS keyring while running on SQLite; while Firestore is active, **encrypted** (Fernet, key derived with HKDF-SHA256 from `AIDRIVEN_ENCRYPTION_KEY` or the service account's private key) in `aidriven_settings/secret__<key>`, mirrored to the keyring as a local cache ([ADR-022](../01-architecture/adr/ADR-022-encrypted-api-keys-in-firestore.md)). A key is never stored in plaintext in SQLite, Firestore or `data/config.json`, and never written to logs (redacted) or exports. The UI treats them as write-only (an empty field keeps the current key) and only shows where a key comes from (saved / env / none). The Firestore service-account JSON is copied to `data/secrets/firestore-sa.json` (git-ignored). See [ADR-016](../01-architecture/adr/ADR-016-secrets-as-built.md).

## P7 — The judge is not the final truth

The judge model gives a **preview** score; the user's score, when set, always prevails. The judge is blind (it never sees the evaluated model's name, provider or profile) and the result page warns when judge and evaluated model are the same model. Results not confirmed by the user are "pending review". See [ADR-015](../01-architecture/adr/ADR-015-judge-and-review-as-built.md).

## P8 — Everything measurable is measured

Every call records tokens (input, output, reasoning, cached), latency, TTFT, tokens/s, estimated cost, retries, turns, tool calls, finish reason and errors/refusals/timeouts. A metric the provider does not report stays `0`/`None`, never invented (e.g. Anthropic does not report reasoning tokens separately). The four dimensions are computed only from recorded data.

## P9 — Everything configurable from the program

Providers, model profiles, tasks, rulesets, agents, suites, judge, sandbox, Firestore, artifact storage and language are edited in the UI. User-editable configuration is persisted locally in `data/config.json` (`AppConfig`, [ADR-012](../01-architecture/adr/ADR-012-sqlite-firestore-and-local-config.md)); environment variables (`AIDRIVEN_*`, provider key fallbacks) only provide startup values and fallbacks. Scoring weights and thresholds are constants in `services/scoring.py`, documented in [../03-modules/evaluation.md](../03-modules/evaluation.md).

## P10 — English codebase, bilingual UI

Code, comments, docstrings, specs, commit messages, README and stored data (enum values, check details, errors, logs) are written in **English**. The UI chrome supports **English and Spanish** (default English) via `ui/locales/{en,es}.json` and `t(key)`; the language is chosen in the header or in *Settings*. User data (task prompts, rubrics, model outputs, judge rationales, names) is never translated. Technical messages produced below the UI (parameter validation, errors, check details) are shown in English. See [ADR-018](../01-architecture/adr/ADR-018-ui-i18n-as-built.md).

## Amendments

- **2026-09-24 (post F0–F7)**: P2 table aligned with the implemented adapters and `AppContext`; P3 clarifies how superseded ADRs are marked; P6/P9 reflect `data/config.json`; P10 no longer requires services to return message keys (technical messages stay English). Motivated by ADR-010…ADR-018.
- **2026-09-24 (user decision)**: P6 renamed "Secrets never in plaintext in the data store" — provider API keys are stored **encrypted** in Firestore while it is active (keyring otherwise); P2 secrets row updated. Motivated by [ADR-022](../01-architecture/adr/ADR-022-encrypted-api-keys-in-firestore.md).
