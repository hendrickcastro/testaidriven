# ADR-021 — Post-F7 hardening: log redaction, interrupted-run recovery, explicit Firestore size errors, judge output budget

- **Status**: Accepted
- **Date**: 2026-09-24
- **Supersedes**: [ADR-016](ADR-016-secrets-as-built.md) §4 (no redaction filter), [ADR-017](ADR-017-run-snapshot-and-rerun-as-built.md) §5 (`partial` not assigned), the silent part of [ADR-012](ADR-012-sqlite-firestore-and-local-config.md) §5 (oversized documents) and the output budget in [ADR-015](ADR-015-judge-and-review-as-built.md) §5

## Context

The as-built review of F0–F7 recorded three gaps in ADR-012, ADR-016 and ADR-017 as accepted consequences: no log redaction (keys inside an SDK exception text would be logged verbatim), runs left `running` forever after a crash, and Firestore documents still above the size limit after truncation failing with a raw API error. All three were cheap to close. In addition, the judge's forced minimum of 4,096 output tokens could exceed a small judge model's own limit.

## Decision

1. **Log redaction** — `logging_setup.RedactingFilter` is attached to **every handler** (file, in-memory capture, console). It formats the record, applies `redact()` to the message and to the exception text, and replaces the record's message when something changed. `redact()` masks: `sk-…` keys (incl. `sk-ant-`, `sk-proj-`, `sk-or-v1-`, ≥ 16 chars), Google `AIza…` keys, PEM `-----BEGIN … PRIVATE KEY----- … -----END … PRIVATE KEY-----` blocks, and the value of `api_key` / `api-key` / `authorization` (incl. `Bearer`) / `x-api-key` / `private_key` assignments (`key: value`, `key=value`, JSON) — all replaced by `[REDACTED]`.
2. **Interrupted-run recovery** — at startup (and whenever the bootstrap runs, e.g. when activating Firestore), `bootstrap.recover_interrupted_runs(repo)` finds runs whose stored status is `running` (the previous process died), sets their `running` results back to `pending`, marks the run **`partial`** with `error = "interrupted: the application stopped while the run was in progress"` and logs a WARNING. *Start / Resume* then continues the run.
3. **Firestore size** — if a document is still above 900,000 bytes after truncating the Result text fields (e.g. a Run with a huge snapshot), `FirestoreRepository` raises `ValueError("<collection>/<id> is <n> bytes, above the Firestore document limit; reduce inline attachments or the number/size of tasks in the run")` instead of letting the API fail.
4. **Judge output budget** — the judge call uses `max_output_tokens = min(max(judge params.max_output_tokens, 4096), judge capabilities.max_output_tokens)`: at least 4,096 when the model allows it, never above the judge model's maximum.

## Alternatives considered

- **Redact only in the file handler**: the Logs page and console would still show secrets.
- **Split large Runs into sub-documents**: not needed at the current scale; the explicit error tells the user what to reduce.

## Consequences

- (+) Secrets are masked even if a third-party exception message contains them (`test_infra.py::test_redaction_masks_keys_and_private_keys`).
- (+) Crashed runs are visibly `partial` and resumable (`test_runner.py::test_interrupted_runs_are_recovered`).
- (−) Redaction is pattern-based: keys of other shapes are only protected by never being logged.
- (−) The recovery assumes a single app process per data store: a run genuinely executing in another process against the same Firestore would be marked `partial` too.
