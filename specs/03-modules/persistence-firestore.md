# Module: Persistence and Firestore

Adapters `SqliteRepository` and `FirestoreRepository`, the Firestore/Storage actions of `AppContext` and the *Settings > Firestore / Storage / Local data* cards. Local SQLite until Firestore is configured from the UI; same document contract in both ([ADR-012](../01-architecture/adr/ADR-012-sqlite-firestore-and-local-config.md), [../02-data-model/firestore-collections.md](../02-data-model/firestore-collections.md)).

## Repository contract (`RepositoryPort`, synchronous)

`get(cls, id)` · `find(cls, *, where: dict | None = None, limit: int | None = None)` · `save(entity)` (upsert, sets `updated_at`) · `save_many(entities)` · `delete(cls, id)` · `delete_where(cls, field, value) -> int` · `get_setting(key)` / `set_setting(key, value)` / `delete_setting(key)` / `list_settings(prefix) -> {id: doc}` (collection `aidriven_settings`).

- `where` holds **single-field equality filters** (`{"run_id": "..."}`); several keys are AND-ed. There is no `order_by`: results come back newest first (`created_at` desc) and callers sort in memory.
- `limit` truncates after sorting.

## SQLite (default)

- File `data/aidriven.db`, `PRAGMA journal_mode=WAL`, one shared connection (`check_same_thread=False`, autocommit) behind an `RLock`.
- **One table per collection**, created on first use:

```sql
CREATE TABLE IF NOT EXISTS "aidriven_results" (id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT);
CREATE INDEX IF NOT EXISTS "aidriven_results_created" ON "aidriven_results"(created_at);
```

- `find` translates filters to `json_extract(data, '$.<field>') = ?` (booleans as 0/1, dicts/lists as JSON) and orders by `created_at DESC`.
- `save_many` runs in one transaction.

## Firestore

- `init_firestore_app(credentials_path, project_id)` initializes (or reuses) the **named** Firebase app `aidriven` — never the default app. `FirestoreRepository` uses `firestore.client(app[, database_id])`; `database_id` is editable (default `(default)`).
- Collections `aidriven_<collection>`, document id = entity id; lists that directly contain lists are wrapped as `{"aidriven_nested_json": "<json>"}` on write and unwrapped on read (`encode_nested`/`decode_nested`) because Firestore rejects nested arrays (this previously made the bootstrap fail on the first activation: seed `sql_result` checks have list-of-list `expected`); filters with `FieldFilter(field, "==", value)`; sorting and `limit` in memory; `save_many` in batches of 400.
- Query failures are logged at ERROR (the message of a `FailedPrecondition` contains the index URL, which the Logs page turns into a hint) and re-raised.

### Configuration flow (*Settings > Firestore*)

1. **Upload JSON** (`AppContext.install_credentials`): must be JSON with `type == "service_account"`, `project_id` and `private_key`; copied to `data/secrets/firestore-sa.json` (git-ignored); `firestore.project_id`/`credentials_path` stored in `data/config.json`; the cached Firebase app is disposed; if no bucket is configured, `storage.bucket = "<project_id>.firebasestorage.app"`. The notification shows project and client email.
2. **Test connection** (`test_firestore`, in a thread): writes `aidriven_health/probe` `{at, by}`, reads it back, deletes it; stores `last_test_ok/at/detail` and shows the detail (`write/read/delete OK on aidriven_health` or the exception).
3. **Activate** (`activate_firestore`): swaps the live repository to Firestore, sets `firestore.enabled = true`, **switches API keys to the encrypted store** (`_enable_cloud_secrets`: keys that exist only locally are uploaded encrypted, [ADR-022](../01-architecture/adr/ADR-022-encrypted-api-keys-in-firestore.md)), runs the bootstrap against Firestore (fake provider/profiles, rulesets, agent and seed suite if not present; seed tasks synced) and reloads the page.
4. **Migrate local → Firestore** (only while Firestore is active): for every entity type (`ENTITY_TYPES`: providers, models, tasks, rulesets, agents, suites, runs, results, artifacts) reads all SQLite documents and upserts them with `save_many`; then uploads missing API keys encrypted; shows the count per collection plus `api_keys`. Local data is left untouched. Internal `aidriven_settings` flags are not migrated.
5. **Deactivate**: back to SQLite (`firestore.enabled = false`) and to the keyring for API keys; no reverse migration.
6. At startup, if `firestore.enabled`, `AppContext` activates Firestore; on failure it logs an ERROR and keeps SQLite.

The header badge shows the active backend (SQLite / Firestore); the dashboard warns while data is only local. **Verified on 2026-09-24** against project `algoritxia`: upload + test succeeded ("write/read/delete OK on aidriven_health"); activation ran the bootstrap and created the demo data and the 14 seed tasks in Firestore (after the nested-array fix); encrypted API keys: only ciphertext at rest, decryption from a fresh cache, cleanup OK.

### Large documents

If the serialized document exceeds **900,000 bytes**, `_to_doc` truncates, in order and until it fits: `response_text`, `reasoning_text` (first 225,000 chars + `…[truncated for Firestore]`), `transcript` (last 5 entries), logging a WARNING per field. There is no spill to the artifact store; the snapshot is never truncated. If the document is **still** above the limit (e.g. a Run with a huge snapshot), the adapter raises `ValueError("<collection>/<id> is <n> bytes, above the Firestore document limit; reduce inline attachments or the number/size of tasks in the run")` instead of a raw API error ([ADR-021](../01-architecture/adr/ADR-021-post-f7-hardening.md)).

## Firebase Storage (*Settings > Storage*)

- Switch **"Save artifacts in the cloud"** (`storage.cloud_enabled`, default off), bucket and prefix (`aidriven/`), *Save* and *Test storage* (upload/download/delete of `<prefix>_health/probe.txt`). Uses the Firestore service account and the same named app ([ADR-014](../01-architecture/adr/ADR-014-firebase-storage-as-built.md)).
- **Prerequisite — not yet met**: verified on 2026-09-24, project `algoritxia` has **no Storage bucket** (`algoritxia.firebasestorage.app` and `algoritxia.appspot.com` both return 404). Storage must be enabled in the Firebase console (Build > Storage > Get started) before turning the toggle on; until then *Test storage* fails and new artifacts could not be saved in the cloud.

## Local data (*Settings > Local data*)

*Backup* copies `data/aidriven.db` to `data/backups/aidriven-<YYYYmmdd-HHMMSS>.db` (plain file copy; WAL content not yet checkpointed may be missing — prefer backing up with no run active).

## Business rules

- Only collections with the prefix `aidriven_` are read or written ([P1](../00-overview/constitution.md)); the prefix is applied by the adapters (`COLLECTION_PREFIX + cls.collection`).
- The shared `config/firestore.rules` and `config/firestore.indexes.json` are never modified or deployed; this project has no client-side Firestore access (the Admin SDK bypasses rules).
- Queries use equality filters only and no `order_by` (no composite indexes).
- The service-account file is never shown again after upload and never logged.

## Acceptance criteria

- [x] On first start without configuration, everything works on SQLite and `data/aidriven.db` is created (`tests/conftest.py` builds every test context this way).
- [x] SQLite round-trip, equality filters (strings and booleans), `delete_where` and settings work (`test_infra.py::test_sqlite_repository_roundtrip_and_filters`).
- [x] Uploading the `algoritxia` service account and testing it writes, reads and deletes the probe in `aidriven_health` (manual, 2026-09-24).
- [x] Nested arrays survive the Firestore encoding round-trip and no array directly contains an array (`test_infra.py::test_firestore_nested_array_encoding_roundtrip`).
- [x] API keys stored through the encrypted store are ciphertext at rest, decrypt on another "machine" with the same key material, stay hidden with a wrong key, and are deleted cleanly (`test_infra.py::test_encrypted_repo_secret_store`; also verified against `algoritxia`).
- [ ] An invalid JSON upload is rejected with a clear message.
- [ ] After activation and migration, the counts per collection in Firestore match SQLite.
- [ ] A result larger than 900 KB is saved with truncated text fields and a WARNING log; a Run that still does not fit raises the explicit `ValueError`.
- [ ] No document is written outside `aidriven_*` (contract test with a recording fake client — not yet written).
- [ ] With the Storage bucket enabled, *Test storage* passes and new artifacts go to `aidriven/<run>/<result>/<name>`.
