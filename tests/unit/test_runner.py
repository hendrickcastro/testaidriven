from aidriven.config import JudgeConfig
from aidriven.context import AppContext
from aidriven.domain.models import (
    Artifact,
    ProviderConnection,
    Result,
    ResultStatus,
    Run,
    RunStatus,
    Task,
)
from aidriven.services.comparison import compare
from aidriven.services.scoring import aggregate_by_model
from tests.conftest import make_profile


async def _run_to_end(ctx: AppContext, run: Run) -> Run:
    live = ctx.runner.start(run.id)
    assert live.task is not None
    await live.task
    final = ctx.repo.get(Run, run.id)
    assert final is not None
    return final


async def test_full_run_with_oracle_and_echo(
    ctx: AppContext, fake_provider: ProviderConnection, answer_task: Task, code_task: Task
) -> None:
    from pathlib import Path

    ref = Path(fake_provider.base_url or "") / "answer-42"
    ref.mkdir()
    (ref / "response.txt").write_text("6*7 is 42.\nFINAL ANSWER: 42\n", encoding="utf-8")
    oracle = make_profile(ctx, fake_provider, "oracle")
    echo = make_profile(ctx, fake_provider, "echo")
    judge = make_profile(ctx, fake_provider, "judge")
    ctx.config.judge = JudgeConfig(enabled=True, model_id=judge.id)
    run = ctx.runner.create_run(
        name="t", task_ids=[answer_task.id, code_task.id], model_ids=[oracle.id, echo.id], repetitions=2
    )
    assert run.total_items == 8
    run = await _run_to_end(ctx, run)
    assert run.status == RunStatus.COMPLETED
    results = ctx.repo.find(Result, where={"run_id": run.id})
    assert len(results) == 8
    by = {(r.task_slug, r.model_name, r.repetition): r for r in results}
    code_oracle = by[("add-fn", "Fake oracle", 0)]
    assert code_oracle.status == ResultStatus.DONE
    assert code_oracle.score.auto == 10
    assert code_oracle.score.judge == 7
    assert code_oracle.artifact_ids
    art = ctx.repo.get(Artifact, code_oracle.artifact_ids[0])
    assert art is not None and art.name == "adder.py"
    # echo always answers 42: correct for the answer task, no artifact for the code task
    assert by[("answer-42", "Fake echo", 0)].score.auto == 10
    assert by[("add-fn", "Fake echo", 1)].score.auto == 0
    aggs = {a.model_name: a for a in aggregate_by_model(results)}
    assert aggs["Fake oracle"].pass_all_k == 1.0
    assert aggs["Fake echo"].pass_at_k == 0.5
    assert aggs["Fake oracle"].dims["intelligence"] > aggs["Fake echo"].dims["intelligence"]


async def test_rerun_single_task_and_results(
    ctx: AppContext, fake_provider: ProviderConnection, answer_task: Task, code_task: Task
) -> None:
    oracle = make_profile(ctx, fake_provider, "oracle")
    run = ctx.runner.create_run(
        name="base", task_ids=[answer_task.id, code_task.id], model_ids=[oracle.id], judge_enabled=False
    )
    run = await _run_to_end(ctx, run)

    only_task = ctx.runner.create_rerun(run.id, scope="tasks", task_slugs=["add-fn"])
    assert only_task.rerun_of == run.id
    assert only_task.total_items == 1
    only_task = await _run_to_end(ctx, only_task)
    res = ctx.repo.find(Result, where={"run_id": only_task.id})
    assert [r.task_slug for r in res] == ["add-fn"]

    first = ctx.repo.find(Result, where={"run_id": run.id})[0]
    by_result = ctx.runner.create_rerun(run.id, scope="results", result_ids=[first.id], repetitions=3)
    assert by_result.total_items == 3
    assert by_result.rerun_items == [first.id]

    full = ctx.runner.create_rerun(run.id)
    assert full.total_items == 2

    cmp = compare(
        [run, only_task],
        {r.id: ctx.repo.find(Result, where={"run_id": r.id}) for r in (run, only_task)},
    )
    assert len(cmp.series) == 2
    row = next(r for r in cmp.rows if r.task_slug == "add-fn")
    delta = row.deltas[cmp.series[1].key]
    assert delta["final"] == 0


async def test_skip_when_capability_missing(ctx: AppContext, fake_provider: ProviderConnection) -> None:
    task = Task(id="t_vis", slug="vis", title="v", category="vision", prompt="look", requires=["vision"])
    ctx.repo.save(task)
    echo = make_profile(ctx, fake_provider, "echo")
    run = ctx.runner.create_run(name="v", task_ids=[task.id], model_ids=[echo.id])
    results = ctx.repo.find(Result, where={"run_id": run.id})
    assert results[0].status == ResultStatus.SKIPPED
    assert run.done_items == 1


async def test_invalid_params_error_without_calling_model(
    ctx: AppContext, fake_provider: ProviderConnection, answer_task: Task
) -> None:
    echo = make_profile(ctx, fake_provider, "echo")
    echo.capabilities.temperature = False
    echo.params.temperature = 0.5
    ctx.repo.save(echo)
    run = await _run_to_end(ctx, ctx.runner.create_run(name="p", task_ids=[answer_task.id], model_ids=[echo.id]))
    res = ctx.repo.find(Result, where={"run_id": run.id})[0]
    assert res.status == ResultStatus.ERROR
    assert "temperature" in (res.error or "")


async def test_user_score_overrides(ctx: AppContext, fake_provider: ProviderConnection, answer_task: Task) -> None:
    echo = make_profile(ctx, fake_provider, "echo")
    run = await _run_to_end(ctx, ctx.runner.create_run(name="u", task_ids=[answer_task.id], model_ids=[echo.id]))
    res = ctx.repo.find(Result, where={"run_id": run.id})[0]
    updated = ctx.runner.set_user_score(res.id, 4, "not convinced")
    assert updated.score.final == 4
    assert updated.score.reviewed


async def test_agent_loop_with_tools(ctx: AppContext, fake_provider: ProviderConnection, answer_task: Task) -> None:
    from aidriven.domain.models import AgentDef, AgentTool

    agent = AgentDef(id="ag", name="a", persona="use tools", tools=[AgentTool.RUN_PYTHON], max_turns=3)
    ctx.repo.save(agent)
    tools = make_profile(ctx, fake_provider, "tools")
    run = await _run_to_end(
        ctx, ctx.runner.create_run(name="a", task_ids=[answer_task.id], model_ids=[tools.id], agent_id=agent.id)
    )
    res = ctx.repo.find(Result, where={"run_id": run.id})[0]
    assert res.status == ResultStatus.DONE
    assert res.metrics.tool_calls == 1
    assert res.metrics.turns == 2
    assert "42" in res.response_text  # the sandbox actually executed print(6*7)


def test_interrupted_runs_are_recovered(ctx: AppContext, fake_provider: ProviderConnection, answer_task: Task) -> None:
    from aidriven.services.bootstrap import recover_interrupted_runs

    echo = make_profile(ctx, fake_provider, "echo")
    run = ctx.runner.create_run(name="i", task_ids=[answer_task.id], model_ids=[echo.id])
    run.status = RunStatus.RUNNING
    ctx.repo.save(run)
    res = ctx.repo.find(Result, where={"run_id": run.id})[0]
    res.status = ResultStatus.RUNNING
    ctx.repo.save(res)
    assert recover_interrupted_runs(ctx.repo) == 1
    assert ctx.repo.get(Run, run.id).status == RunStatus.PARTIAL  # type: ignore[union-attr]
    assert ctx.repo.get(Result, res.id).status == ResultStatus.PENDING  # type: ignore[union-attr]


async def test_backfill_estimates_anthropic_reasoning(ctx: AppContext, answer_task: Task) -> None:
    from types import SimpleNamespace

    from aidriven.domain.models import ModelProfile, ProviderKind, RunSnapshot
    from aidriven.services import backfill

    conn = ProviderConnection(id="prov_a", kind=ProviderKind.ANTHROPIC, name="a")
    ctx.repo.save(conn)
    prof = ModelProfile(id="m_opus", name="Opus", provider_id=conn.id, model="claude-opus-5-5")
    ctx.repo.save(prof)
    run = Run(name="old", snapshot=RunSnapshot(tasks=[answer_task], models=[prof]))
    ctx.repo.save(run)
    res = Result(
        run_id=run.id,
        task_id=answer_task.id,
        task_slug=answer_task.slug,
        task_version=1,
        model_id=prof.id,
        model_name=prof.name,
        status=ResultStatus.DONE,
        response_text="visible answer",
    )
    res.metrics.output_tokens = 500
    ctx.repo.save(res)

    class Messages:
        async def count_tokens(self, model: str, messages: list[dict[str, str]]) -> SimpleNamespace:
            return SimpleNamespace(input_tokens=10 if len(messages) == 1 else 130)  # visible answer = 120

    ctx.adapter_for = lambda _p: SimpleNamespace(client=SimpleNamespace(messages=Messages()))  # type: ignore[method-assign]
    assert len(backfill.candidates(ctx, run)) == 1
    assert await backfill.estimate_anthropic_reasoning(ctx, run) == 1
    got = ctx.repo.get(Result, res.id)
    assert got is not None and got.metrics.reasoning_tokens == 380 and got.metrics.reasoning_estimated
    assert backfill.candidates(ctx, run) == []
