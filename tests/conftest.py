from __future__ import annotations

from pathlib import Path

import pytest

from aidriven.adapters.secrets import MemorySecretStore
from aidriven.config import EnvSettings
from aidriven.context import AppContext
from aidriven.domain.models import (
    Capabilities,
    Check,
    CheckKind,
    GenerationParams,
    ModelProfile,
    ProviderConnection,
    ProviderKind,
    Task,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def ctx(tmp_path: Path) -> AppContext:
    env = EnvSettings(data_dir=tmp_path / "data")
    context = AppContext(env, secrets=MemorySecretStore())
    context.config.sandbox.mode = "process"
    context.save_config()
    return context


@pytest.fixture
def fake_provider(ctx: AppContext, tmp_path: Path) -> ProviderConnection:
    oracle_dir = tmp_path / "oracle"
    oracle_dir.mkdir()
    conn = ProviderConnection(
        id="prov_fake", kind=ProviderKind.FAKE, name="fake", base_url=str(oracle_dir), max_retries=0
    )
    ctx.repo.save(conn)
    return conn


def make_profile(ctx: AppContext, provider: ProviderConnection, model: str, name: str | None = None) -> ModelProfile:
    profile = ModelProfile(
        id=f"m_{model}",
        name=name or f"Fake {model}",
        provider_id=provider.id,
        model=model,
        capabilities=Capabilities(tools=True, vision=False),
        params=GenerationParams(max_output_tokens=1024),
    )
    ctx.repo.save(profile)
    return profile


@pytest.fixture
def answer_task(ctx: AppContext) -> Task:
    task = Task(
        id="t_answer",
        slug="answer-42",
        title="Answer 42",
        category="reasoning",
        prompt="What is 6*7? End with FINAL ANSWER: <n>",
        checks=[
            Check(id="c1", kind=CheckKind.EXACT, name="exact 42", expected="42"),
            Check(
                id="c2",
                kind=CheckKind.REGEX,
                name="final line",
                pattern=r"(?m)^FINAL ANSWER: \d+$",
                dimension="conformity",
            ),
        ],
    )
    ctx.repo.save(task)
    return task


@pytest.fixture
def code_task(ctx: AppContext, fake_provider: ProviderConnection) -> Task:
    task = Task(
        id="t_code",
        slug="add-fn",
        title="Add function",
        category="algorithms",
        prompt="Write add(a, b) in adder.py",
        artifact_names=["adder.py"],
        expected_artifact="python",
        checks=[
            Check(
                id="c_tests",
                kind=CheckKind.PYTHON_TESTS,
                name="hidden tests",
                code=(
                    "from aidriven_harness import case, report, load_module\n"
                    "m = load_module('adder.py')\n"
                    "@case('small')\n"
                    "def _():\n    assert m.add(2, 3) == 5\n"
                    "@case('negative')\n"
                    "def _():\n    assert m.add(-2, -3) == -5\n"
                    "report()\n"
                ),
            )
        ],
    )
    ctx.repo.save(task)
    folder = Path(fake_provider.base_url or "") / "add-fn"
    folder.mkdir()
    (folder / "adder.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return task
