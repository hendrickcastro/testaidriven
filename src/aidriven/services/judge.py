"""LLM judge: a configurable (stronger) model pre-scores each result 1-10 as a preview the user can correct.

Prompt id `judge.score` (specs/05-llm/prompts.md). The judge is blind: it never sees which model produced
the answer.
"""

from __future__ import annotations

import json
import logging
import re

from aidriven.adapters.llm.base import BaseAdapter
from aidriven.domain.models import (
    CheckResult,
    GenerationParams,
    JudgeCriterion,
    JudgeVerdict,
    Metrics,
    ModelProfile,
    Task,
)
from aidriven.ports import LLMRequest, Message

log = logging.getLogger(__name__)

MAX_ANSWER_CHARS = 60_000

JUDGE_SYSTEM = (
    "You are a rigorous, impartial expert evaluator of AI-generated answers. You grade strictly against the task, "
    "the rubric and the reference. You never reward length, confidence or style over correctness. "
    "Reply with a single JSON object and nothing else."
)

JUDGE_TEMPLATE = """Grade the candidate answer to the task below on a 1-10 scale.

Scale: 10 = fully correct, complete and meets every requirement; 7-9 = correct with minor issues;
4-6 = partially correct or significant omissions; 2-3 = mostly wrong; 1 = wrong, empty or refused.

<task>
{prompt}
</task>

<rubric>
{rubric}
</rubric>

<reference>
{reference}
</reference>

<automated_checks>
{checks}
</automated_checks>

<candidate_answer>
{answer}
</candidate_answer>

Automated checks are reliable evidence of functional correctness: do not contradict a failed hidden test
unless the check itself is clearly broken. Return JSON exactly in this shape:
{{"score": <number 1-10>, "criteria": [{{"name": "<criterion>", "score": <1-10>, "comment": "<short>"}}],
"rationale": "<2-4 sentences>"}}"""


def summarize_checks(checks: list[CheckResult]) -> str:
    if not checks:
        return "(no automated checks)"
    lines = []
    for c in checks:
        state = "SKIPPED" if c.skipped else ("PASS" if c.passed else f"FAIL ({c.score:.0%})")
        lines.append(f"- [{c.dimension}] {c.name}: {state} — {c.detail.splitlines()[0] if c.detail else ''}")
    return "\n".join(lines)


def parse_verdict(text: str) -> dict[str, object]:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("judge did not return JSON")
    data = json.loads(candidate[start : end + 1])
    if not isinstance(data, dict) or "score" not in data:
        raise ValueError("judge JSON has no score")
    return data


class JudgeService:
    def __init__(self, adapter: BaseAdapter, profile: ModelProfile) -> None:
        self.adapter = adapter
        self.profile = profile

    async def judge(self, task: Task, answer: str, checks: list[CheckResult], evaluated_model: str) -> JudgeVerdict:
        answer = answer if len(answer) <= MAX_ANSWER_CHARS else answer[:MAX_ANSWER_CHARS] + "\n…[truncated]"
        prompt = JUDGE_TEMPLATE.format(
            prompt=task.prompt.strip(),
            rubric=task.rubric.strip() or "Correctness, completeness and adherence to every instruction.",
            reference=task.reference_answer.strip() or "(none provided)",
            checks=summarize_checks(checks),
            answer=answer.strip() or "(empty answer)",
        )
        params = self.profile.params.model_copy(deep=True)
        params.stream = False
        params.max_output_tokens = min(max(params.max_output_tokens, 4096), self.profile.capabilities.max_output_tokens)
        request = LLMRequest(
            model=self.profile.model,
            messages=[Message(role="user", text=prompt)],
            params=params if params else GenerationParams(),
            system=JUDGE_SYSTEM,
            metadata={"task_slug": task.slug, "role": "judge"},
        )
        verdict = JudgeVerdict(
            judge_model_id=self.profile.id,
            judge_model_name=self.profile.name,
            score=0,
            same_model_warning=self.profile.model == evaluated_model,
        )
        try:
            resp = await self.adapter.generate(request)
            verdict.metrics = Metrics(
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
                reasoning_tokens=resp.reasoning_tokens,
                latency_ms=resp.latency_ms,
                cost_usd=self.profile.pricing.cost(resp.input_tokens, resp.output_tokens, resp.cached_tokens),
            )
            data = parse_verdict(resp.text)
            verdict.score = max(1.0, min(10.0, float(str(data["score"]))))
            verdict.rationale = str(data.get("rationale", ""))[:4000]
            criteria = data.get("criteria")
            for c in criteria if isinstance(criteria, list) else []:
                if isinstance(c, dict) and "name" in c:
                    verdict.criteria.append(
                        JudgeCriterion(
                            name=str(c["name"]),
                            score=float(c.get("score", 0) or 0),
                            comment=str(c.get("comment", "")),
                        )
                    )
        except Exception as exc:
            log.warning("judge failed on %s: %s", task.slug, exc)
            verdict.error = f"{type(exc).__name__}: {exc}"[:1000]
        return verdict
