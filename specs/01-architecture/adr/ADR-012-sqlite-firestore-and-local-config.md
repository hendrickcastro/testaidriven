# ADR-012 — SQLite locally, Firestore when configured; user configuration in `data/config.json`

- **Status**: Accepted — §5 (oversized documents) amended by [ADR-021](ADR-021-post-f7-hardening.md); §2 (`aidriven_settings` = internal flags only) extended by [ADR-022](ADR-022-encrypted-api-keys-in-firestore.md) (encrypted API keys)
- **Date**: 2026-09-24
- **Supersedes**: [ADR-003](ADR-003-sqlite-local-then-firestore.md)

## Context

ADR-003 planned a single SQLite `documents` table, a `data/bootstrap.json` that selects the backend, application settings in `aidriven_settings/<id>` documents, a migration that keeps newer remote documents, and spilling of large fields to the artifact store. The implementation needed the user-editable configuration (language, Firestore, storage, judge, sandbox) **before** any repository exists — it holds the Firestore connection itself — so it moved to a local JSON file. Large-field spilling was replaced by truncation.

## Decision

1. **User configuration** is `AppConfig` (`config.py`), persisted atomically (write `.tmp` + rename, thread lock) as **`data/config.json`**:
   `language` (`en`|`es`) · `firestore {enabled, credentials_path, project_id, database_id="(default)", last_test_ok, last_test_at, last_test_detail}` · `storage {cloud_enabled=false, bucket, prefix="aidriven/"}` · `judge {enabled=true, model_id}` · `sandbox {mode, python_image, playwright_image, memory_mb, cpus, pids_limit, default_timeout_s}`. It never contains secrets (the service account lives in `data/secrets/`, API keys in the keyring).
2. **`aidriven_settings`** (in whichever repository is active) holds only **internal flags**: `bootstrap` (`{"done": true}` once the first-start data exists) and `seed_suite_known` (`{"ids": [...]}`, the seed task ids already offered to the built-in suite).
3. **`SqliteRepository`** (default): `data/aidriven.db`, WAL mode, **one table per collection** named with the full prefix (`aidriven_results`…), columns `(id TEXT PRIMARY KEY, data TEXT, created_at TEXT)` plus an index on `created_at`. Filters use `json_extract(data, '$.<field>') = ?` (several fields are AND-ed); rows come back newest first. One connection shared across threads behind an `RLock`.
4. **`FirestoreRepository`**: Admin SDK, named app `aidriven`, collections `aidriven_<collection>`, document id = `Entity.id`, optional non-default `database_id`. Equality filters only (`FieldFilter(field, "==", value)`), sorting by `created_at` and `limit` applied **in memory**. Batches of 400 in `save_many`.
5. **Large documents**: if the JSON of a document exceeds **900,000 bytes**, the adapter truncates, in order and until it fits, `response_text` and `reasoning_text` (kept to the first 225,000 characters + `…[truncated for Firestore]`) and `transcript` (last 5 entries), logging a WARNING. Nothing is moved to the artifact store. SQLite stores documents whole.
6. **Configuration flow** (*Settings > Firestore*, methods of `AppContext`): upload the service-account JSON (validated: `type == "service_account"`, `project_id`, `private_key`) → copied to `data/secrets/firestore-sa.json` and, if no bucket is set, `storage.bucket` defaults to `<project_id>.firebasestorage.app` → **Test connection** (write, read back and delete `aidriven_health/probe`) → **Activate** (swaps the live repository, sets `firestore.enabled`, runs the bootstrap against Firestore) → optional **Migrate local → Firestore** (upserts every entity type from SQLite; local data is kept) → **Deactivate** (back to SQLite, no reverse migration). At startup, `firestore.enabled` re-activates Firestore; if that fails the app logs an ERROR and stays on SQLite.
7. **SQLite backup**: *Settings > Local data > Backup* copies `data/aidriven.db` to `data/backups/aidriven-<YYYYmmdd-HHMMSS>.db`.
8. No composite index is ever required; the shared `config/firestore.rules` and `config/firestore.indexes.json` are neither modified nor deployed ([P1](../../00-overview/constitution.md)).

## Alternatives considered

- **Backend selection in `data/bootstrap.json` + settings in Firestore** (ADR-003): two sources of truth and a chicken-and-egg problem for the Firestore settings themselves.
- **Spilling large fields to the artifact store**: exact round-trip, but couples persistence to artifact storage and needs rehydration on every read; results above 900 KB are rare (very long agent transcripts).

## Consequences

- (+) The app starts and is fully configurable offline; switching backends is a UI action.
- (+) Per-collection SQLite tables mirror Firestore collections 1:1, so migration is a plain upsert.
- (−) Configuration does not travel with the data: a second machine pointing at the same Firestore needs its own `data/config.json` (and keys).
- (−) Truncated fields are lossy in Firestore (the local SQLite copy, if the run was executed there, keeps the full text).
- (−) Migration overwrites remote documents with the same id (last writer wins).
