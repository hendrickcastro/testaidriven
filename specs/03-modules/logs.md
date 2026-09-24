# Module: Logs

`logging_setup.py` (`setup_logging`, `LogCapture`) and the *Logs* page (`/logs`). Centralizes application logs, makes failures visible without a console, and turns Firestore missing-index errors into copyable links.

## Capture

- `setup_logging(data/logs)` (idempotent) configures the root logger at **INFO** with three handlers sharing the format `%(asctime)s %(levelname)-7s %(name)s: %(message)s`:
  - `RotatingFileHandler` → `data/logs/aidriven.log` (5,000,000 bytes × 5 backups), UTF-8.
  - `_CaptureHandler` → the in-memory `LogCapture` ring (last **3,000** entries: timestamp, level, logger, message + formatted traceback).
  - Console (`StreamHandler`).
- Noisy loggers (`httpx`, `httpcore`, `urllib3`, `google.auth`, `grpc`, `docker`) are set to WARNING.
- Encrypted-secret decryption failures (wrong key) are logged at ERROR without the value.
- Logs are always in English ([ADR-018](../01-architecture/adr/ADR-018-ui-i18n-as-built.md)).
- **Redaction**: a `RedactingFilter` on every handler runs `redact()` over the message and the exception text before anything is written or shown: `sk-…` keys (incl. `sk-ant-`, `sk-proj-`, `sk-or-v1-`), Google `AIza…` keys, PEM private-key blocks and the values of `api_key` / `authorization` (incl. `Bearer`) / `x-api-key` / `private_key` assignments become `[REDACTED]` ([ADR-021](../01-architecture/adr/ADR-021-post-f7-hardening.md)).

## Firestore index detection

- Every captured message is scanned with `https://console\.(?:firebase|cloud)\.google\.com/[^\s"'<>)]*(?:create_composite|indexes)[^\s"'<>)]*`. `FirestoreRepository.find` logs query failures at ERROR, so a `FailedPrecondition` with its creation URL is always captured.
- Each URL becomes an `IndexHint` (first/last seen, count, first 300 chars of context), deduplicated by URL. Hints live **in memory** (lost on restart) and can be dismissed.
- After creating an index, the user records it in [../02-data-model/firestore-collections.md](../02-data-model/firestore-collections.md); the app never creates indexes or edits `config/firestore.indexes.json`.

## *Logs* page

- **Index hints** card (top): URL, count and last seen, buttons *Copy* (clipboard), *Open* (new tab), *Dismiss*.
- **Log** card: minimum-level filter (DEBUG…CRITICAL, default INFO), text search (message or logger), *Auto-refresh* switch (every 3 s), *Clear view* (empties the ring buffer; files untouched). Shows the last 500 matching entries, newest first, in a code block.

## Business rules

- Provider, sandbox, Firestore, storage and judge failures are logged with the exception text.
- The log level is fixed at INFO (no setting yet).

## Acceptance criteria

- [x] A message containing a `create_composite` URL creates one hint; the same URL twice gives count 2 (`test_infra.py::test_log_capture_extracts_index_urls`).
- [x] `setup_logging` is idempotent (`test_infra.py::test_logging_setup_is_idempotent`).
- [x] API keys, Google keys, PEM private keys and `api_key=` values are masked (`test_infra.py::test_redaction_masks_keys_and_private_keys`).
- [ ] The log file rotates at 5 MB and the page keeps working.
- [ ] Filtering by level and text shows only matching entries.
