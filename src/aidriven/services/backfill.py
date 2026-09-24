"""Backfill metrics that older runs did not record, without calling the model again.

Anthropic bills thinking inside `output_tokens`. Results recorded before thinking tokens were read from
`usage.output_tokens_details` have reasoning_tokens = 0; we estimate them as
    output_tokens − tokens(visible answer)
with the free `count_tokens` endpoint, and flag them as estimated (`Metrics.reasoning_estimated`).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aidriven.domain.models import ModelProfile, ProviderConnection, ProviderKind, Result, ResultStatus, Run

if TYPE_CHECKING:
    from aidriven.context import AppContext

log = logging.getLogger(__name__)
PROBE = "x"


def candidates(ctx: AppContext, run: Run) -> list[Result]:
    """Done Anthropic results of the run with output tokens but no reasoning tokens recorded."""
    anthropic_models = {
        m.id
        for m in run.snapshot.models
        if (conn := ctx.repo.get(ProviderConnection, m.provider_id)) and conn.kind == ProviderKind.ANTHROPIC
    }
    return [
        r
        for r in ctx.repo.find(Result, where={"run_id": run.id})
        if r.model_id in anthropic_models
        and r.status == ResultStatus.DONE
        and r.metrics.reasoning_tokens == 0
        and r.metrics.output_tokens > 0
    ]


async def estimate_anthropic_reasoning(ctx: AppContext, run: Run) -> int:
    """Estimate missing Anthropic thinking tokens for a run. Returns how many results were updated."""
    todo = candidates(ctx, run)
    if not todo:
        return 0
    profiles = {m.id: m for m in run.snapshot.models}
    updated = 0
    for r in todo:
        profile: ModelProfile = profiles[r.model_id]
        adapter = ctx.adapter_for(profile)
        client = adapter.client  # type: ignore[attr-defined]
        try:
            base = await client.messages.count_tokens(
                model=profile.model, messages=[{"role": "user", "content": PROBE}]
            )
            with_answer = await client.messages.count_tokens(
                model=profile.model,
                messages=[{"role": "user", "content": PROBE}, {"role": "assistant", "content": r.response_text or " "}],
            )
        except Exception as exc:
            log.warning("count_tokens failed for %s: %s", r.id, exc)
            continue
        visible = max(with_answer.input_tokens - base.input_tokens, 0)
        r.metrics.reasoning_tokens = max(r.metrics.output_tokens - visible, 0)
        r.metrics.reasoning_estimated = True
        ctx.repo.save(r)
        updated += 1
    log.info("estimated reasoning tokens for %d Anthropic result(s) of run %s", updated, run.name)
    return updated
