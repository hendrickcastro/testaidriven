"""Run orchestration: create runs (from a suite, ad hoc, or as a re-run), execute them concurrently,
cancel, resume, and re-evaluate single results. See specs/03-modules/execution.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from aidriven.adapters.llm.base import looks_like_refusal
from aidriven.domain.models import (
    AgentDef,
    Artifact,
    Metrics,
    ModelProfile,
    Result,
    ResultStatus,
    Ruleset,
    Run,
    RunSnapshot,
    RunStatus,
    Suite,
    Task,
    utcnow,
)
from aidriven.ports import LLMError, LLMResponse
from aidriven.services.agent import AgentRunner
from aidriven.services.artifacts import KIND_MIME, extract_files, kind_for
from aidriven.services.judge import JudgeService
from aidriven.services.prompting import build_request, missing_capabilities
from aidriven.services.scoring import auto_score

if TYPE_CHECKING:
    from aidriven.context import AppContext

log = logging.getLogger(__name__)
TERMINAL = {
    ResultStatus.DONE,
    ResultStatus.ERROR,
    ResultStatus.REFUSED,
    ResultStatus.TIMEOUT,
    ResultStatus.SKIPPED,
}


@dataclass
class LiveRun:
    run_id: str
    total: int = 0
    done: int = 0
    active: dict[str, str] = field(default_factory=dict)  # result id -> "task · model"
    events: deque[str] = field(default_factory=lambda: deque(maxlen=300))
    task: asyncio.Task[None] | None = None
    cancelled: bool = False

    def log(self, msg: str) -> None:
        self.events.append(f"{datetime.now().strftime('%H:%M:%S')}  {msg}")


class RunnerService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.live: dict[str, LiveRun] = {}

    # ------------------------------------------------------------------ creation

    def _load(self, cls: type[Any], ids: list[str]) -> list[Any]:
        repo = self.ctx.repo
        items = [repo.get(cls, i) for i in ids]
        return [i for i in items if i is not None]

    def create_run(
        self,
        *,
        name: str,
        task_ids: list[str],
        model_ids: list[str],
        ruleset_ids: list[str] | None = None,
        agent_id: str | None = None,
        repetitions: int = 1,
        concurrency: int = 4,
        task_timeout_s: float = 900.0,
        judge_enabled: bool = True,
        suite_id: str | None = None,
    ) -> Run:
        tasks = self._load(Task, task_ids)
        models = self._load(ModelProfile, model_ids)
        if not tasks or not models:
            raise ValueError("a run needs at least one task and one model")
        rulesets = self._load(Ruleset, ruleset_ids or [])
        agent = self.ctx.repo.get(AgentDef, agent_id) if agent_id else None
        snapshot = RunSnapshot(
            tasks=tasks, models=models, rulesets=rulesets, agent=agent, judge=self.ctx.judge_profile()
        )
        run = Run(
            name=name,
            suite_id=suite_id,
            snapshot=snapshot,
            repetitions=repetitions,
            concurrency=concurrency,
            task_timeout_s=task_timeout_s,
            judge_enabled=judge_enabled,
        )
        self._create_items(run)
        return run

    def create_from_suite(self, suite: Suite, name: str | None = None) -> Run:
        return self.create_run(
            name=name or f"{suite.name} · {utcnow():%Y-%m-%d %H:%M}",
            task_ids=suite.task_ids,
            model_ids=suite.model_ids,
            ruleset_ids=suite.ruleset_ids,
            agent_id=suite.agent_id,
            repetitions=suite.repetitions,
            concurrency=suite.concurrency,
            task_timeout_s=suite.task_timeout_s,
            judge_enabled=suite.judge_enabled,
            suite_id=suite.id,
        )

    def create_rerun(
        self,
        run_id: str,
        *,
        scope: Literal["full", "tasks", "results"] = "full",
        task_slugs: list[str] | None = None,
        model_names: list[str] | None = None,
        result_ids: list[str] | None = None,
        use_current_versions: bool = False,
        repetitions: int | None = None,
    ) -> Run:
        """Repeat a run completely, only some tasks/models, or only specific results. Creates a NEW run."""
        orig = self.ctx.repo.get(Run, run_id)
        if orig is None:
            raise ValueError(f"run {run_id} not found")
        snap = orig.snapshot.model_copy(deep=True)
        if use_current_versions:
            snap.tasks = [self.ctx.repo.get(Task, t.id) or t for t in snap.tasks]
            snap.models = [self.ctx.repo.get(ModelProfile, m.id) or m for m in snap.models]
            snap.judge = self.ctx.judge_profile() or snap.judge
        elif snap.judge is None:
            # The judge evaluates, it doesn't generate: a run without one picks up the judge configured now.
            snap.judge = self.ctx.judge_profile()
        pairs: set[tuple[str, str]] | None = None
        if scope == "tasks":
            if task_slugs:
                snap.tasks = [t for t in snap.tasks if t.slug in task_slugs]
            if model_names:
                snap.models = [m for m in snap.models if m.name in model_names]
        elif scope == "results":
            results = [r for r in (self.ctx.repo.get(Result, i) for i in result_ids or []) if r is not None]
            pairs = {(r.task_id, r.model_id) for r in results}
            snap.tasks = [t for t in snap.tasks if any(p[0] == t.id for p in pairs)]
            snap.models = [m for m in snap.models if any(p[1] == m.id for p in pairs)]
        if not snap.tasks or not snap.models:
            raise ValueError("nothing selected to re-run")
        label = {"full": "re-run", "tasks": "re-run (tasks)", "results": "re-run (results)"}[scope]
        run = Run(
            name=f"{orig.name} · {label}",
            suite_id=orig.suite_id,
            snapshot=snap,
            repetitions=repetitions or orig.repetitions,
            concurrency=orig.concurrency,
            task_timeout_s=orig.task_timeout_s,
            judge_enabled=orig.judge_enabled,
            rerun_of=orig.id,
            rerun_scope=scope,
            rerun_items=(result_ids or []) if scope == "results" else [*(task_slugs or []), *(model_names or [])],
            snapshot_mode="current" if use_current_versions else "original",
        )
        self._create_items(run, pairs)
        return run

    def _create_items(self, run: Run, pairs: set[tuple[str, str]] | None = None) -> None:
        results: list[Result] = []
        for rep in range(run.repetitions):
            for task in run.snapshot.tasks:
                for model in run.snapshot.models:
                    if pairs is not None and (task.id, model.id) not in pairs:
                        continue
                    r = Result(
                        run_id=run.id,
                        task_id=task.id,
                        task_slug=task.slug,
                        task_version=task.version,
                        model_id=model.id,
                        model_name=model.name,
                        repetition=rep,
                    )
                    missing = missing_capabilities(task, model)
                    if missing:
                        r.status = ResultStatus.SKIPPED
                        r.error = f"model lacks required capability: {', '.join(missing)}"
                    results.append(r)
        run.total_items = len(results)
        run.done_items = sum(r.status in TERMINAL for r in results)
        self.ctx.repo.save(run)
        self.ctx.repo.save_many(results)

    # ------------------------------------------------------------------ execution

    def start(self, run_id: str) -> LiveRun:
        live = self.live.get(run_id)
        if live and live.task and not live.task.done():
            return live
        live = LiveRun(run_id)
        self.live[run_id] = live
        live.task = asyncio.create_task(self._execute(run_id, live))
        return live

    def cancel(self, run_id: str) -> None:
        live = self.live.get(run_id)
        if live:
            live.cancelled = True
            if live.task:
                live.task.cancel()

    def is_running(self, run_id: str) -> bool:
        live = self.live.get(run_id)
        return bool(live and live.task and not live.task.done())

    async def _execute(self, run_id: str, live: LiveRun) -> None:
        repo = self.ctx.repo
        run = repo.get(Run, run_id)
        if run is None:
            return
        results = repo.find(Result, where={"run_id": run_id})
        pending = [r for r in results if r.status not in TERMINAL]
        task_order = {t.id: i for i, t in enumerate(run.snapshot.tasks)}
        pending.sort(key=lambda r: (r.repetition, task_order.get(r.task_id, 0), r.model_name))
        live.total = len(results)
        live.done = len(results) - len(pending)
        run.status = RunStatus.RUNNING
        run.started_at = run.started_at or utcnow()
        run.error = None
        repo.save(run)
        live.log(f"run started: {len(pending)} pending of {len(results)}")
        sem = asyncio.Semaphore(max(1, run.concurrency))
        tasks = {t.id: t for t in run.snapshot.tasks}
        models = {m.id: m for m in run.snapshot.models}

        async def worker(result: Result) -> None:
            async with sem:
                if live.cancelled:
                    return
                label = f"{result.task_slug} · {result.model_name} #{result.repetition + 1}"
                live.active[result.id] = label
                try:
                    await self.process(run, result, tasks[result.task_id], models[result.model_id])
                except asyncio.CancelledError:
                    result.status = ResultStatus.PENDING
                    repo.save(result)
                    raise
                except Exception as exc:  # never let one item kill the run
                    log.exception("result %s crashed", result.id)
                    result.status = ResultStatus.ERROR
                    result.error = f"internal error: {exc}"
                    repo.save(result)
                finally:
                    live.active.pop(result.id, None)
                live.done += 1
                live.log(f"{result.status.value:<8} {label}  score={result.score.final}")
                run.done_items = live.done
                repo.save(run)

        try:
            await asyncio.gather(*(worker(r) for r in pending))
            run.status = RunStatus.CANCELLED if live.cancelled else RunStatus.COMPLETED
        except asyncio.CancelledError:
            run.status = RunStatus.CANCELLED
            live.log("run cancelled")
        except Exception as exc:
            run.status = RunStatus.FAILED
            run.error = str(exc)
            log.exception("run %s failed", run_id)
        finally:
            run.finished_at = utcnow()
            run.done_items = live.done
            repo.save(run)
            live.log(f"run {run.status.value}")

    async def process(self, run: Run, result: Result, task: Task, profile: ModelProfile) -> Result:
        """Execute one (task × model × repetition): generate → artifacts → checks → judge → score."""
        repo = self.ctx.repo
        result.status = ResultStatus.RUNNING
        result.started_at = utcnow()
        result.error = None
        repo.save(result)
        problems = profile.validate_params()
        if problems:
            return self._finish(result, ResultStatus.ERROR, "invalid parameters: " + "; ".join(problems))
        try:
            adapter = self.ctx.adapter_for(profile)
        except Exception as exc:
            return self._finish(result, ResultStatus.ERROR, f"provider not available: {exc}")
        agent = run.snapshot.agent
        request = build_request(
            task, profile, run.snapshot.rulesets, agent, {"run_id": run.id, "repetition": result.repetition}
        )
        files: dict[str, str] = {}
        responses: list[LLMResponse] = []
        try:
            if agent is not None and profile.capabilities.tools:
                outcome = await asyncio.wait_for(
                    AgentRunner(adapter, self.ctx.sandbox, agent, task).run(request),
                    timeout=run.task_timeout_s,
                )
                responses, text, files = outcome.responses, outcome.final_text, dict(outcome.files)
                result.transcript = outcome.transcript
                result.metrics.tool_calls = outcome.tool_calls
                all_text = "\n\n".join(r.text for r in responses)
            else:
                resp = await asyncio.wait_for(adapter.generate(request), timeout=run.task_timeout_s)
                responses, text, all_text = [resp], resp.text, resp.text
        except TimeoutError:
            return self._finish(result, ResultStatus.TIMEOUT, f"no answer within {run.task_timeout_s:.0f}s")
        except LLMError as exc:
            return self._finish(result, ResultStatus.ERROR, str(exc))

        result.metrics = self._metrics(responses, profile, result.metrics.tool_calls)
        result.response_text = text
        result.reasoning_text = "\n\n".join(r.reasoning_text for r in responses if r.reasoning_text)
        result.finish_reason = responses[-1].finish_reason if responses else None

        for f in extract_files(all_text, task.artifact_names):
            files.setdefault(f.name, f.content)
        result.artifact_ids = self._store_artifacts(run, result, files)

        refused = any(r.refusal for r in responses) or (not files and looks_like_refusal(text))
        result.checks = await self.ctx.check_engine.run_all(task.checks, text, files)
        result.score.auto = auto_score(result.checks)
        if refused:
            result.score.auto = 0.0 if task.checks else None
            return self._finish(result, ResultStatus.REFUSED, "the model refused the task")

        judge_profile = run.snapshot.judge
        if run.judge_enabled and judge_profile is not None:
            result.judge = await self._judge(judge_profile, task, text, result, profile)
            if result.judge and result.judge.error is None:
                result.score.judge = result.judge.score
        return self._finish(result, ResultStatus.DONE, None)

    async def _judge(
        self, judge_profile: ModelProfile, task: Task, text: str, result: Result, profile: ModelProfile
    ) -> Any:
        try:
            adapter = self.ctx.adapter_for(judge_profile)
        except Exception as exc:
            log.warning("judge unavailable: %s", exc)
            return None
        # Artifacts travel inside the response text (fenced blocks), so the judge sees them.
        return await JudgeService(adapter, judge_profile).judge(task, text, result.checks, profile.model)

    def _finish(self, result: Result, status: ResultStatus, error: str | None) -> Result:
        result.status = status
        result.error = error
        result.finished_at = utcnow()
        self.ctx.repo.save(result)
        return result

    @staticmethod
    def _metrics(responses: list[LLMResponse], profile: ModelProfile, tool_calls: int) -> Metrics:
        m = Metrics(turns=len(responses), tool_calls=tool_calls)
        for r in responses:
            m.input_tokens += r.input_tokens
            m.output_tokens += r.output_tokens
            m.reasoning_tokens += r.reasoning_tokens
            m.cached_tokens += r.cached_tokens
            m.latency_ms += r.latency_ms
            m.retries += r.retries
        if responses and responses[0].ttft_ms is not None:
            m.ttft_ms = responses[0].ttft_ms
        gen_ms = m.latency_ms - (m.ttft_ms or 0)
        if m.output_tokens and gen_ms > 0:
            m.output_tokens_per_s = round(m.output_tokens / (gen_ms / 1000), 2)
        m.cost_usd = round(profile.pricing.cost(m.input_tokens, m.output_tokens, m.cached_tokens), 6)
        return m

    def _store_artifacts(self, run: Run, result: Result, files: dict[str, str]) -> list[str]:
        ids = []
        store = self.ctx.artifact_store()
        for name, content in files.items():
            data = content.encode("utf-8")
            kind = kind_for(name)
            mime = KIND_MIME.get(kind, "text/plain")
            try:
                location = store.put(run.id, result.id, name, data, mime)
            except Exception as exc:
                log.error("storing artifact %s failed on %s store: %s", name, store.kind, exc)
                continue
            art = Artifact(
                run_id=run.id,
                result_id=result.id,
                name=name,
                kind=kind,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                storage=store.kind,
                location=location,
                mime=mime,
            )
            self.ctx.repo.save(art)
            ids.append(art.id)
        return ids

    # ------------------------------------------------------------------ re-evaluation

    async def reevaluate(self, result_id: str, rejudge: bool = True) -> Result:
        """Re-run checks (and optionally the judge) on an existing answer, without calling the model again."""
        repo = self.ctx.repo
        result = repo.get(Result, result_id)
        if result is None:
            raise ValueError("result not found")
        run = repo.get(Run, result.run_id)
        if run is None:
            raise ValueError("run not found")
        task = next(t for t in run.snapshot.tasks if t.id == result.task_id)
        profile = next(m for m in run.snapshot.models if m.id == result.model_id)
        files = self.load_files(result)
        result.checks = await self.ctx.check_engine.run_all(task.checks, result.response_text, files)
        result.score.auto = auto_score(result.checks)
        judge_profile = self.ctx.judge_profile() or run.snapshot.judge
        if rejudge and judge_profile is not None and result.status == ResultStatus.DONE:
            result.judge = await self._judge(judge_profile, task, result.response_text, result, profile)
            if result.judge and result.judge.error is None:
                result.score.judge = result.judge.score
        repo.save(result)
        return result

    def load_files(self, result: Result) -> dict[str, str]:
        files: dict[str, str] = {}
        for aid in result.artifact_ids:
            art = self.ctx.repo.get(Artifact, aid)
            if art is None:
                continue
            try:
                files[art.name] = self.ctx.artifact_store_for(art.storage).get(art.location).decode("utf-8", "replace")
            except Exception as exc:
                log.warning("cannot load artifact %s: %s", art.name, exc)
        return files

    def set_user_score(self, result_id: str, score: float | None, comment: str = "") -> Result:
        result = self.ctx.repo.get(Result, result_id)
        if result is None:
            raise ValueError("result not found")
        if score is not None and not 1 <= score <= 10:
            raise ValueError("score must be between 1 and 10")
        result.score.user = score
        result.score.user_comment = comment
        result.score.reviewed = score is not None
        self.ctx.repo.save(result)
        return result

    def accept_judge(self, result_id: str) -> Result:
        result = self.ctx.repo.get(Result, result_id)
        if result is None or result.score.judge is None:
            raise ValueError("no judge score to accept")
        return self.set_user_score(result_id, result.score.judge, result.score.user_comment)

    def delete_run(self, run_id: str) -> None:
        repo = self.ctx.repo
        for art in repo.find(Artifact, where={"run_id": run_id}):
            with contextlib.suppress(Exception):
                self.ctx.artifact_store_for(art.storage).delete(art.location)
        repo.delete_where(Artifact, "run_id", run_id)
        repo.delete_where(Result, "run_id", run_id)
        repo.delete(Run, run_id)
