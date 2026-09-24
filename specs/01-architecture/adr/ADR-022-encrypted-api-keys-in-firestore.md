# ADR-022 — Provider API keys stored encrypted in Firestore when it is active

- **Status**: Accepted
- **Date**: 2026-09-24
- **Supersedes**: the "keys never in the data store / keys do not travel with the data" part of [ADR-016](ADR-016-secrets-as-built.md) (§1, §4 and consequences); amends constitution [P6](../../00-overview/constitution.md#p6--secrets-never-in-plaintext-in-the-data-store)

## Context

ADR-016 kept API keys only in the OS keyring, so a second machine pointing at the same Firestore had to re-enter every key. **User decision (2026-09-24)**: when Firestore is the active backend, provider keys must live in Firestore too, so the configuration travels with the data — but never readable by someone who only has read access to the documents.

## Decision

1. `EncryptedRepoSecretStore` (`adapters/secrets.py`) implements `SecretStorePort` on top of the active repository: key `provider:<id>:api_key` is stored as document **`aidriven_settings/secret__<secret key>`** (`/` replaced by `_`) = `{"value_enc": <Fernet token>, "algo": "fernet-hkdf-sha256"}`. The plaintext never reaches Firestore.
2. **Encryption key**: `derive_fernet_key(material)` = HKDF-SHA256 (32 bytes, salt `aidriven-secrets-v1`, info `api-keys`) → Fernet (AES-128-CBC + HMAC-SHA256). The material is `AIDRIVEN_ENCRYPTION_KEY` (`EnvSettings.encryption_key`, see `.env.example`) if set, otherwise the **`private_key` of the uploaded service account** — so any machine with the same service account (or the same explicit key) can decrypt.
3. **Cache**: every write is mirrored to the local store (OS keyring, or memory without a keyring backend); reads try Firestore first, then the local cache. A token that cannot be decrypted (wrong key) is **not revealed**: an ERROR is logged ("cannot decrypt secret …: wrong key") and the cache is used. Deleting removes both the document and the cached value.
4. **Selection** (`AppContext.secrets`): the encrypted store while Firestore is active; the keyring store (`local_secrets`) on SQLite. `activate_firestore` (also at startup when Firestore is enabled) calls `_enable_cloud_secrets`, which **uploads keys that exist only locally** for every known connection (Firestore and SQLite ones) and clears the adapter cache; `migrate_local_to_firestore` reports the number of uploaded keys as `api_keys`; `deactivate_firestore` returns to the keyring.
5. `RepositoryPort` gains `delete_setting(key)` and `list_settings(prefix)` (both adapters).
6. The env-var fallback (ADR-016 §2) and write-only UI (§3) are unchanged; the connection form states whether keys are stored "encrypted in Firestore" or "only locally".
7. `cryptography` is used through the Fernet/HKDF primitives (currently installed transitively via `google-auth`).

Verified on 2026-09-24 against project `algoritxia`: only ciphertext at rest, decryption from a fresh cache works, cleanup OK (`tests/unit/test_infra.py::test_encrypted_repo_secret_store` covers round-trip, ciphertext-only storage and the wrong-key case offline).

## Alternatives considered

- **Keyring only** (ADR-016): safest at rest, but keys do not follow the data to another machine.
- **Plaintext in Firestore protected by security rules**: the Admin SDK bypasses rules and the project is shared with other applications.
- **Cloud KMS / Secret Manager**: stronger key management, but new GCP infrastructure and permissions for a single-user tool.

## Consequences

- (+) A new machine with the service account (or `AIDRIVEN_ENCRYPTION_KEY`) gets working connections after activating Firestore.
- (+) Reading the `aidriven_settings` documents alone does not reveal any key.
- (−) Whoever holds the service-account private key (or the explicit encryption key) can decrypt all keys; the service account already grants full project access, so the trust boundary does not grow — but rotating the service account without `AIDRIVEN_ENCRYPTION_KEY` makes stored keys undecryptable (they must be re-entered; local caches keep working).
- (−) Keys now exist in two places (Firestore ciphertext + local cache).
