"""Every seed task must be solvable: its reference solution scores 1.0 on every check (skipped checks aside),
and an empty answer must not. Runs the real CheckEngine in the process sandbox."""

from __future__ import annotations

from pathlib import Path

import pytest

from aidriven.adapters.llm.fake_adapter import oracle_response
from aidriven.adapters.sandbox import ProcessSandbox
from aidriven.services.artifacts import extract_files
from aidriven.services.checks import CheckEngine
from aidriven.services.task_io import load_seed_tasks

SOLUTIONS = Path(__file__).resolve().parents[1] / "fixtures" / "seed_solutions"
TASKS = load_seed_tasks()
ENGINE = CheckEngine(ProcessSandbox())  # type: ignore[arg-type]


@pytest.mark.parametrize("task", TASKS, ids=[t.slug for t in TASKS])
async def test_reference_solution_passes_every_check(task) -> None:  # type: ignore[no-untyped-def]
    response = oracle_response(task.slug, SOLUTIONS)
    assert response, f"no reference solution for {task.slug}"
    files = {f.name: f.content for f in extract_files(response, task.artifact_names)}
    for name in task.artifact_names:
        assert name in files, f"{task.slug}: reference does not deliver {name}"
    results = await ENGINE.run_all(task.checks, response, files)
    failed = [f"{r.name}: {r.score:.2f} {r.detail[:300]}" for r in results if not r.skipped and r.score < 0.999]
    assert not failed, f"{task.slug}: " + " | ".join(failed)


@pytest.mark.parametrize("task", TASKS, ids=[t.slug for t in TASKS])
async def test_empty_answer_does_not_pass(task) -> None:  # type: ignore[no-untyped-def]
    results = await ENGINE.run_all(task.checks, "I don't know.\nFINAL ANSWER: 0", {})
    scored = [r for r in results if not r.skipped]
    assert scored, f"{task.slug}: every check was skipped"
    assert any(r.score < 0.999 for r in scored), f"{task.slug}: a non-answer passes all checks"
