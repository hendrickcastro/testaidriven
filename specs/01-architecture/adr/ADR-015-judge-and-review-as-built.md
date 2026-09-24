# ADR-015 — Judge preview and user final score (as built)

- **Status**: Accepted — §5 output budget amended by [ADR-021](ADR-021-post-f7-hardening.md)
- **Date**: 2026-09-24
- **Supersedes**: [ADR-006](ADR-006-judge-preview-user-final-score.md)

## Context

ADR-006 defined three scores, a blind judge with JSON output and two retries, an *Accept* action that keeps the computed score, and a same-model warning based on `(provider kind, model)`. The implementation keeps the three scores and the blind judge; the review actions and some judge details differ.

## Decision

1. `Score` has `auto` (0..10 from weighted checks), `judge` (1..10 preview), `user` (1..10, prevails), `user_comment`, `reviewed`. **`Score.final`** = `user` if set; else `round((judge + auto) / 2, 2)` if both exist; else whichever exists; else `None`. It is a computed property, never stored.
2. The judge is the `ModelProfile` selected in *Settings > Judge* (`AppConfig.judge.model_id`, with an `enabled` switch; also settable from *Models* with "Use as judge"). `create_run` freezes it in `RunSnapshot.judge`; each run/suite can disable it (`judge_enabled`).
3. **Blind**: the judge request (`services/judge.py`, prompt `judge.score` in [../../05-llm/prompts.md](../../05-llm/prompts.md)) contains the task prompt, rubric, reference answer, a summary of the automatic checks and the candidate answer (truncated to 60,000 characters; artifacts are included as the fenced blocks of the answer). It never contains the evaluated profile, model or provider.
4. `same_model_warning = (judge profile.model == evaluated profile.model)` (model id string comparison); the result page shows a warning; the run proceeds.
5. The judge must return JSON `{score, criteria[{name, score, comment}], rationale}`; the parser accepts a fenced block or the outermost `{…}`; `score` is clamped to [1, 10]. **No retries**: on any failure the verdict keeps `error` and `Score.judge` stays `None`. The judge call is non-streaming with `max_output_tokens ≥ 4096`.
6. The judge's own usage (tokens, latency, cost with the judge profile's pricing) is stored in `JudgeVerdict.metrics` and never added to the evaluated model's metrics.
7. Review actions (result page and *Review* queue): **Save score** (slider 1–10, step 0.5, optional comment) sets `user` and `reviewed=true`; **Accept judge** sets `user = judge` and `reviewed=true`; **Clear** sets `user=None`, `reviewed=false`. **Re-evaluate** re-runs checks and judge on the stored answer without calling the evaluated model (never touches `user`).
8. "Pending review" = a `done` result with `reviewed=false`; the dashboard, the run KPIs and the *Review* queue count them.

## Alternatives considered

- **Accept = mark reviewed but keep the computed final** (ADR-006): subtle for users; copying the judge score makes the accepted value explicit.
- **Retrying invalid judge JSON**: not needed with current judge models; failures are visible and *Re-evaluate* retries on demand.

## Consequences

- (+) Every number is traceable: auto from checks, judge preview with rationale and cost, user override.
- (−) The same-model warning does not catch the same model served by two different providers under different ids.
- (−) *Re-evaluate* uses the currently configured judge (falling back to the snapshot judge), so a re-judged result may use a different judge than the rest of its run.
