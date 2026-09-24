"""First-start data: seed tasks, a zero-cost fake provider with its profiles, default rulesets, an agent
and a suite with the seed battery. Idempotent; seed tasks are re-synced on every start."""

from __future__ import annotations

import logging

from aidriven.adapters.llm.catalog import find_entry
from aidriven.domain.models import (
    AgentDef,
    AgentTool,
    GenerationParams,
    ModelProfile,
    ProviderConnection,
    ProviderKind,
    Result,
    ResultStatus,
    Ruleset,
    Run,
    RunStatus,
    Suite,
    Task,
)
from aidriven.ports import RepositoryPort
from aidriven.services.task_io import sync_seed_tasks

log = logging.getLogger(__name__)
BOOTSTRAP_KEY = "bootstrap"

DEFAULT_RULESETS = [
    Ruleset(
        id="rules_rigor",
        name="Rigor",
        description="Verify before answering; state assumptions.",
        content=(
            "Work carefully. Before giving a final answer, verify it against every requirement of the task. "
            "If something is ambiguous, state your assumption explicitly and continue."
        ),
    ),
    Ruleset(
        id="rules_concise",
        name="Concise output",
        description="No filler; deliver only what is asked.",
        content="Be concise. Do not add introductions, summaries or explanations that the task does not ask for.",
    ),
]

DEFAULT_AGENT = AgentDef(
    id="agent_python_engineer",
    name="Python engineer (sandbox)",
    description="Can run Python and save files in the sandbox before answering.",
    persona=(
        "You are a meticulous software engineer. Use the tools to write your files and test them in the sandbox "
        "before giving your final answer. Iterate until your own tests pass."
    ),
    tools=[AgentTool.WRITE_FILE, AgentTool.RUN_PYTHON, AgentTool.RUN_HTML, AgentTool.READ_ATTACHMENT],
    max_turns=10,
)


def _fake_profile(provider_id: str, model: str, name: str) -> ModelProfile:
    entry = find_entry(ProviderKind.FAKE, model)
    assert entry is not None
    return ModelProfile(
        id=f"model_fake_{model}",
        name=name,
        provider_id=provider_id,
        model=model,
        description=entry.notes,
        capabilities=entry.capabilities,
        pricing=entry.pricing,
        params=GenerationParams(max_output_tokens=4096),
        tags=["fake"],
    )


def recover_interrupted_runs(repo: RepositoryPort) -> int:
    """A run cannot be running at startup: the previous process died. Mark it partial and requeue its
    in-flight results so "Start / resume" continues where it stopped."""
    recovered = 0
    for run in repo.find(Run, where={"status": RunStatus.RUNNING.value}):
        for result in repo.find(Result, where={"run_id": run.id, "status": ResultStatus.RUNNING.value}):
            result.status = ResultStatus.PENDING
            repo.save(result)
        run.status = RunStatus.PARTIAL
        run.error = "interrupted: the application stopped while the run was in progress"
        repo.save(run)
        recovered += 1
    if recovered:
        log.warning("recovered %d interrupted run(s); resume them from the Runs page", recovered)
    return recovered


def bootstrap(repo: RepositoryPort) -> None:
    recover_interrupted_runs(repo)
    changed = sync_seed_tasks(repo)
    if changed:
        log.info("seed tasks synced: %d", changed)
    if (repo.get_setting(BOOTSTRAP_KEY) or {}).get("done"):
        _sync_seed_suite(repo)
        return
    fake = ProviderConnection(id="provider_fake", kind=ProviderKind.FAKE, name="Fake (offline demo)")
    repo.save(fake)
    for model, name in (
        ("oracle", "Fake oracle"),
        ("echo", "Fake echo"),
        ("judge", "Fake judge"),
        ("tools", "Fake tools"),
    ):
        repo.save(_fake_profile(fake.id, model, name))
    for rs in DEFAULT_RULESETS:
        repo.save(rs)
    repo.save(DEFAULT_AGENT)
    seed_ids = [t.id for t in repo.find(Task, where={"source": "seed"})]
    repo.save(
        Suite(
            id="suite_seed_hard",
            name="Hard battery (seed)",
            description="The built-in battery of very hard, automatically verifiable tasks.",
            task_ids=sorted(seed_ids),
            model_ids=["model_fake_oracle", "model_fake_echo"],
            repetitions=1,
            concurrency=4,
        )
    )
    repo.set_setting(BOOTSTRAP_KEY, {"done": True})
    log.info("bootstrap data created")


def _sync_seed_suite(repo: RepositoryPort) -> None:
    """New seed tasks join the built-in battery automatically (tasks the user removed from it stay removed)."""
    suite = repo.get(Suite, "suite_seed_hard")
    if suite is None:
        return
    known = set((repo.get_setting("seed_suite_known") or {}).get("ids", suite.task_ids))
    seed_ids = {t.id for t in repo.find(Task, where={"source": "seed"})}
    new = sorted(seed_ids - known)
    if new:
        suite.task_ids = [*suite.task_ids, *new]
        repo.save(suite)
        log.info("seed battery: added %d new seed tasks", len(new))
    repo.set_setting("seed_suite_known", {"ids": sorted(known | seed_ids)})
