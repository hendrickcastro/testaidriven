# ADR-016 — Secrets in the OS keyring (as built)

- **Status**: Accepted — §4 (no redaction filter) superseded by [ADR-021](ADR-021-post-f7-hardening.md); key storage (§1, §4 "never in the data store") superseded by [ADR-022](ADR-022-encrypted-api-keys-in-firestore.md)
- **Date**: 2026-09-24
- **Supersedes**: [ADR-007](ADR-007-secrets-in-os-keyring.md)

## Context

ADR-007 specified the keyring store, an env fallback, masked write-only keys and a log redaction filter. The implementation keeps keys out of every data store and log line it writes itself, adds an in-memory fallback when no keyring backend exists, and does not (yet) install a redaction filter.

## Decision

1. `KeyringSecretStore` (`adapters/secrets.py`): service `aidriven`, key `provider:<connection_id>:api_key` (`ProviderConnection.secret_key`). On Windows this is the Credential Manager. If the `keyring` backend cannot be initialized, `AppContext` falls back to `MemorySecretStore` (keys live only for the process lifetime) and logs a WARNING.
2. `resolve_api_key` returns `(key, source)`: the saved key (`source="saved"`), else the env var of the provider kind (`"env"`): `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `OLLAMA_API_KEY`, `OPENAI_COMPATIBLE_API_KEY`; else `(None, None)`. `fake` needs no key; `ollama`/`openai_compatible` work without one.
3. The connection dialog is write-only: an empty key field keeps the current key; *Clear key* deletes it; the connections table shows only the source (saved / env / none). `mask()` (`••••••••` + last 4 chars) is available for display.
4. Keys are never written to SQLite, Firestore, `data/config.json`, exports, snapshots or log messages produced by the app. **No log redaction filter is installed**: protection relies on never formatting keys into messages.
5. The Firestore service-account JSON is copied to `data/secrets/firestore-sa.json`; `data/`, `config/algoritxia.json`, `*.sa.json` and `*serviceAccount*.json` are git-ignored, and pre-commit runs `detect-private-key`.

## Alternatives considered

- **Encrypted field in the store**: needs a master key somewhere; the keyring solves that for a desktop app.
- **Redaction filter on every handler** (ADR-007): still desirable as defence in depth (see [../../08-roadmap/phases.md](../../08-roadmap/phases.md#next)).

## Consequences

- (+) Moving data to Firestore or exporting it cannot leak keys.
- (−) Without a keyring backend, keys must be re-entered each start or provided via env vars.
- (−) An SDK exception whose text contained a key would be logged verbatim (none of the used SDKs is known to do this).
