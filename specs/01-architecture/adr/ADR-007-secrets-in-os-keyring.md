# ADR-007 — Secrets in the OS keyring, never in the data store

- **Status**: Superseded by [ADR-016](ADR-016-secrets-as-built.md) (2026-09-24)
- **Date**: 2026-09-24

## Context

The app needs API keys for up to eight providers plus the Firestore service-account JSON. The data store may be Firestore, shared with other applications and synced to the cloud; exports and migrations copy documents around; logs are shown in the UI. A leaked key has direct monetary cost. The repository already contains a service-account key (`config/algoritxia.json`) that must never be committed.

## Decision

1. `SecretStorePort` with `KeyringSecretStore`: service name `aidriven`, key `provider:<connection_id>:api_key` (`ProviderConnection.secret_key`). On Windows this is the Credential Manager.
2. **Read-only env fallback** by provider kind when the keyring has no value: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `AZURE_OPENAI_API_KEY`. `ollama`, `openai_compatible` and `fake` may run without a key. The UI shows the source ("keyring" / "env" / "none").
3. Keys are **write-only** in the UI: the field shows a masked value (`sk-…a1b2`, last 4 characters), saving an empty value keeps the current one, and an explicit *Delete key* button removes it.
4. Keys are never written to the repository, logs, exports, snapshots or error messages; the log formatter redacts anything matching known key patterns (`sk-`, `sk-ant-`, `AIza`, long base64-like tokens in headers).
5. The Firestore service-account JSON is copied to `data/secrets/firestore-sa.json`; `data/`, `config/algoritxia.json`, `*.sa.json` and `*serviceAccount*.json` are git-ignored.

## Alternatives considered

- **Encrypted field in the store (AES-GCM with a master key)**: used by ContextAdmin, but the master key must then live somewhere; the OS keyring solves exactly that for a single-user desktop app.
- **`.env` file only**: easy but plain text on disk and not editable from the UI.

## Consequences

- (+) Moving the data store to Firestore cannot leak keys.
- (+) Keys survive reinstalling the app or deleting `data/`.
- (−) Keys do not travel with the data to another machine; they must be re-entered (by design).
- (−) Headless Linux without a keyring backend falls back to env vars only; documented in [../../07-deployment/local.md](../../07-deployment/local.md).
