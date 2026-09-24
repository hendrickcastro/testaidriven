# ADR-014 — Optional Firebase Storage for artifacts (as built)

- **Status**: Accepted
- **Date**: 2026-09-24
- **Supersedes**: [ADR-005](ADR-005-optional-firebase-storage.md)

## Context

ADR-005 assumed the Firebase project `algoritxia` already had a Storage bucket, required a passing *Test bucket* before the toggle could be enabled and planned an *Upload local artifacts to the cloud* action. The implementation keeps the toggle and per-artifact location but not the gating or the bulk upload. **Verified on 2026-09-24: project `algoritxia` has no Storage bucket yet** — both `algoritxia.firebasestorage.app` and `algoritxia.appspot.com` return 404.

## Decision

1. Two `ArtifactStorePort` adapters (`adapters/artifact_stores.py`):
   - `LocalArtifactStore` (default): `data/artifacts/<run_id>/<result_id>/<name>`; the stored `location` is the path relative to `data/artifacts/`; reads refuse locations that escape the root.
   - `FirebaseStorageArtifactStore`: blob `<prefix><run_id>/<result_id>/<name>` (prefix default `aidriven/`) in the configured bucket, using the same named Firebase app (and service account) as Firestore.
   Path segments are passed through `safe_name` (flatten `/`/`\`, drop `.`/`..`, charset `[A-Za-z0-9._-]`, ≤ 150 chars).
2. The toggle **"Save artifacts in the cloud"** (`AppConfig.storage.cloud_enabled`, default **off**) selects the store for **new** artifacts only. It is independent of the test: turning it on with a missing bucket makes artifact saves fail (logged as ERROR, the result is kept without that artifact).
3. The bucket name defaults to **`<project_id>.firebasestorage.app`** when a service account is uploaded and no bucket is set; it is editable in *Settings > Storage* together with the prefix.
4. *Test storage* uploads, downloads and deletes `<prefix>_health/probe.txt`.
5. Metadata always goes to `aidriven_artifacts` (`storage`, `location`, `sha256`, `size`, `mime`); each artifact is read back from the store it records, so mixed locations work. Deleting a run deletes every artifact from its own store, then the metadata, results and run.
6. There is no bulk "upload local artifacts" action.
7. **Operational prerequisite**: before enabling the toggle, Storage must be enabled for the project in the Firebase console (which creates the default bucket), then *Test storage* must pass.

## Alternatives considered

- **Block the toggle until the test passes** (ADR-005): safer UX, not implemented; the test button and the logged errors cover it for a single user.
- **Content inline in Firestore**: breaks the 1 MiB document limit.

## Consequences

- (+) Local by default, no cloud cost or dependency unless enabled.
- (−) Until the bucket exists, cloud artifacts cannot be used at all (see [../../08-roadmap/phases.md](../../08-roadmap/phases.md#next)).
- (−) Artifacts produced while the toggle was on stay in the cloud; switching it off does not move them back.
