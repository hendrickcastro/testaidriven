# ADR-005 — Optional Firebase Storage for artifacts

- **Status**: Superseded by [ADR-014](ADR-014-firebase-storage-as-built.md) (2026-09-24)
- **Date**: 2026-09-24

## Context

The request asks for "an artifact repository, local and/or in the cloud, configurable". Artifacts are files produced by models (Python programs, HTML pages, JSON, SQL...), from a few bytes to a couple of MiB, and they must be retrievable for reproduction and comparison. Firestore documents are limited to 1 MiB and are the wrong place for file content. The Firebase project `algoritxia` already provides a Storage bucket reachable with the same service account.

## Decision

1. `ArtifactStorePort` with two adapters:
   - `LocalArtifactStore` (default): `data/artifacts/<run_id>/<result_id>/<name>`.
   - `FirebaseStorageStore`: blob path `aidriven/<run_id>/<result_id>/<name>` in a configurable bucket (default `<project_id>.appspot.com` or `<project_id>.firebasestorage.app`, as read from the service account / entered by the user).
2. Enabled by the toggle **"Save artifacts in the cloud"** in *Settings > Storage* (`aidriven_settings/storage.cloud_artifacts`, default `false`). It requires Firestore credentials to be configured and a successful *Test bucket* (upload + download + delete of `aidriven/_health/<uuid>`).
3. Metadata always goes to `aidriven_artifacts` (`storage`, `location`, `sha256`, `size`, `mime`); content only to the store. Each artifact records where it lives, so switching the toggle affects **new** artifacts only; old ones stay readable from their original store.
4. Optional action **"Upload local artifacts to the cloud"** copies existing local artifacts and updates their `storage`/`location` after verifying the sha256.

## Alternatives considered

- **Content inline in Firestore**: breaks the 1 MiB limit and inflates read costs.
- **Cloud always on**: contradicts "local and/or cloud" and makes the app depend on network.
- **Generic S3-compatible store**: possible later as another adapter; not needed now.

## Consequences

- (+) Local by default, no cloud cost unless enabled.
- (+) The same service account covers Firestore and Storage.
- (−) Mixed locations after toggling; handled by per-artifact `storage`/`location`.
- (−) Deleting a run must delete from both stores; `delete_run` is implemented per adapter and called for each store present in the run's artifacts.
