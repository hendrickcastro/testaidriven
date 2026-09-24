# ADR-003 — SQLite locally, Firestore when configured (prefix `aidriven_`)

- **Status**: Superseded by [ADR-012](ADR-012-sqlite-firestore-and-local-config.md) (2026-09-24)
- **Date**: 2026-09-24

## Context

The request requires "a connection to Firestore to store relevant data" with the prefix `aidriven_` on every collection. The Firebase project `algoritxia` is shared with other applications (`comunio_*`, `llmchat_*`, `ContextAdmin_*`), each identified by its collection prefix, and it has shared `firestore.rules`/`firestore.indexes.json`. At the same time, the app must work on first launch without credentials, and the user wants to configure Firestore from the UI by uploading the service-account JSON.

## Decision

1. Two `RepositoryPort` adapters with the **same document contract** ([../../02-data-model/firestore-collections.md](../../02-data-model/firestore-collections.md)):
   - `SqliteRepository` (default): `data/aidriven.db`, one table `documents(collection, id, data JSON, created_at, updated_at)`; `collection` stores the full prefixed name (`aidriven_runs`) so migration is 1:1.
   - `FirestoreRepository`: Admin SDK, collections `aidriven_<collection>`, document id = `Entity.id`.
2. Configuration from the UI (*Settings > Firestore*): upload JSON → copy to `data/secrets/firestore-sa.json` (git-ignored) → **Test connection** (write + read + delete in `aidriven_health`) → **Activate** → optional **Migrate local → Firestore**. The active backend is recorded in `data/bootstrap.json` (it cannot live in the repository it selects).
3. **No composite indexes**: queries use at most one equality filter and no `order_by`; sorting and pagination happen in memory. Still, *Logs* captures any `FailedPrecondition` with its index-creation URL ([../../03-modules/logs.md](../../03-modules/logs.md)).
4. This project **does not modify or deploy** the shared `config/firestore.rules` or `config/firestore.indexes.json`. The Admin SDK bypasses security rules; there is no client-side Firestore access.
5. `firebase-admin` is imported only in `adapters/repo/firestore_repo.py` (and the Storage adapter); sync calls run in `asyncio.to_thread`.

## Alternatives considered

- **Firestore only**: simplest code path, but unusable until credentials exist and impossible to run tests offline.
- **SQLite only**: fully local, but ignores the explicit requirement and cross-device access to results.
- **Postgres**: better querying, but new infrastructure for a single-user tool.

## Consequences

- (+) Works out of the box; Firestore is an opt-in upgrade with a migration path.
- (+) Queries without composite indexes keep the shared index file untouched.
- (−) In-memory sorting/filtering limits scalability to thousands of results per query; acceptable at personal scale (a run with 15 tasks × 5 models × 5 repetitions = 375 results).
- (−) Firestore's 1 MiB document limit requires spilling large fields to the artifact store ([../../03-modules/persistence-firestore.md](../../03-modules/persistence-firestore.md)).
