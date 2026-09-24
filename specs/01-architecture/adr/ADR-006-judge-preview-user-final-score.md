# ADR-006 — Judge model as preview, user score as final

- **Status**: Superseded by [ADR-015](ADR-015-judge-and-review-as-built.md) (2026-09-24)
- **Date**: 2026-09-24

## Context

The request: "tasks must award a score from 1 to 10 for the user to rate, but a configured superior model (configurable from the UI) can score first as a preview, with the user able to correct it". Many hard tasks have objective checks (hidden tests, exact answers), others (HTML apps, explanations) need judgment. LLM judges are useful but biased: they favour their own family, long answers and known model names.

## Decision

1. Three scores per result (`Score`): `auto` (0..10 from weighted checks), `judge` (1..10 preview), `user` (1..10, prevails). `Score.final` = `user` if set; otherwise `round(mean(judge, auto), 2)` if both exist; otherwise whichever exists; otherwise `null`.
2. The judge is a `ModelProfile` chosen in *Settings > Judge* (`aidriven_settings/judge.model_id`) and frozen in `RunSnapshot.judge`. Each suite can disable it (`Suite.judge_enabled`).
3. **Blind judging**: the prompt `judge.score` ([../../05-llm/prompts.md](../../05-llm/prompts.md)) contains task prompt, rubric, reference answer, response, artifacts and check outcomes, but never the evaluated model's name, provider or profile.
4. If judge and evaluated model resolve to the same `(provider kind, model)`, the verdict carries `same_model_warning=true` and the UI shows a badge; the run still proceeds.
5. The judge returns JSON (`score`, `criteria[]`, `rationale`); invalid JSON is retried twice; persistent failure stores `JudgeVerdict.error` and leaves `Score.judge = null`.
6. A result is **"pending review"** while `Score.reviewed = false`. In *Review* the user can *Accept* (sets `reviewed=true`, keeps `final` computed) or *Override* (sets `user` 1..10, optional `user_comment`, `reviewed=true`). Judge metrics (tokens, cost) are stored separately in `JudgeVerdict.metrics` and never counted in the evaluated model's performance.

## Alternatives considered

- **Judge score as final**: faster, but violates the request and hides judge bias.
- **User-only scoring**: unbiased but unmanageable for hundreds of results.
- **Multi-judge ensemble**: better calibration, higher cost; possible later by allowing several judge profiles (not in scope).

## Consequences

- (+) Objective checks and a human keep the judge honest; every number is traceable to its source.
- (+) Comparisons can be filtered to "reviewed only".
- (−) Unreviewed runs mix judge and auto scores; the UI always shows how many results are pending review.
