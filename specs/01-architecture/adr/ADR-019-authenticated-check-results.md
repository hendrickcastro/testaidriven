# ADR-019 — Authenticated check results (single-use nonce)

- **Status**: Accepted
- **Date**: 2026-09-24
- **Supersedes**: — (new decision; it changes the sandbox output contract described in [ADR-013](ADR-013-docker-sandbox-as-built.md) §5)

## Context

Sandbox checks report through stdout: the harness prints an `AIDRIVEN_RESULT {json}` (or `AIDRIVEN_SKIP {json}`) line and the check engine parses it. The code under test is the evaluated model's artifact, loaded into the **same process** as the check (`load_module`). An artifact could therefore print a fake `AIDRIVEN_RESULT {"score": 1.0}` line at import time, or a fake skip line to have a failing check excluded, and the engine (which took the first matching line) would accept it. A benchmark whose score can be written by the candidate is worthless.

## Decision

1. For every sandbox check, `CheckEngine` generates a random single-use nonce (`secrets.token_hex(16)`) and writes it into the job work dir as **`_aidriven_nonce`**.
2. The harness reads **and deletes** that file when it is imported — i.e. by the check script, before any model code is loaded — and keeps the value only inside a closure (`_make_emitter(nonce)` → `_emit`); it is not stored as a module attribute.
3. Result and skip lines carry the nonce: `AIDRIVEN_RESULT <nonce> {json}` and `AIDRIVEN_SKIP <nonce> {json}`. They are written with `os.write(1, …)` directly to file descriptor 1, so a monkeypatched `print`/`sys.stdout` cannot swallow or rewrite them.
4. The engine accepts only lines whose nonce matches:
   - lines with a wrong or missing nonce are **ignored** and counted in the detail as *"ignored N forged result line(s) printed by the artifact"*;
   - **more than one** authenticated line (result or skip) is treated as **tampering** → score 0 (*"tampering: more than one authenticated result line"*);
   - exactly one authenticated skip → skipped; exactly one authenticated result → its score; none → 0 (*"no result"*).
5. If the nonce file is missing (e.g. a harness used outside the engine), the harness emits with the placeholder `-`, which the engine never accepts.

## Alternatives considered

- **Take the last result line** (instead of the first): defeated by an `atexit` hook in the artifact.
- **Run the artifact in a separate process and exchange results over a pipe/file**: stronger, but every check script would need an RPC layer to call into the candidate's module; the harness API (`load_module`, direct calls in `case`) would change for all tasks.
- **Sign the result with an HMAC key in the environment**: the artifact can read the environment; a file deleted before model code runs cannot be read later.

## Consequences

- (+) Printing a fake result or skip line no longer changes the score; attempts are visible in the check detail (`test_checks.py::test_forged_result_line_from_artifact_is_ignored`, `test_artifact_cannot_read_the_nonce`).
- (+) An artifact that digs the emitter out of the harness and emits a result still produces a second authenticated line when `report()` runs → tampering → 0 (`test_artifact_introspecting_harness_is_flagged`).
- (−) **Residual risk**: the emitter is reachable from Python (`aidriven_harness._emit`, or the closure cell). A deliberately adversarial artifact that emits a forged authenticated line **and** prevents `report()` from running (e.g. `os._exit(0)` at import) would still be accepted as the only authenticated line. Closing this fully needs process separation between the check and the candidate code (see [../../08-roadmap/phases.md](../../08-roadmap/phases.md#next)). This is an integrity measure against accidental or opportunistic gaming, not a guarantee against a model that targets the harness specifically.
- (−) Check scripts must not print lines starting with `AIDRIVEN_RESULT`/`AIDRIVEN_SKIP` themselves; they use `report()`/`skip()`.
