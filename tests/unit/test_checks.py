from aidriven.adapters.sandbox import ProcessSandbox
from aidriven.domain.models import Check, CheckKind
from aidriven.services.checks import CheckEngine, extract_final_answer, parse_number

ENGINE = CheckEngine(ProcessSandbox())  # type: ignore[arg-type]


def test_final_answer_extraction_takes_last() -> None:
    text = "FINAL ANSWER: 1\nwait, no\n**FINAL ANSWER:** 2"
    assert extract_final_answer(text) == "2"


def test_parse_number_handles_separators() -> None:
    assert parse_number("about 1,234,567.5 units") == 1234567.5


async def test_exact_and_numeric() -> None:
    exact = Check(kind=CheckKind.EXACT, name="e", expected="Red House")
    numeric = Check(kind=CheckKind.NUMERIC, name="n", expected=3.14159, tolerance=1e-3)
    ok = await ENGINE.run_all([exact], "FINAL ANSWER: red   house.", {})
    assert ok[0].passed
    num = await ENGINE.run_all([numeric], "FINAL ANSWER: 3.1416", {})
    assert num[0].passed
    bad = await ENGINE.run_all([numeric], "no answer", {})
    assert not bad[0].passed


async def test_json_schema_on_last_block() -> None:
    check = Check(kind=CheckKind.JSON_SCHEMA, name="j", schema={"type": "object", "required": ["a"]})
    res = await ENGINE.run_all([check], 'x\n```json\n{"a": 1}\n```', {})
    assert res[0].passed
    res = await ENGINE.run_all([check], '```json\n{"b": 1}\n```', {})
    assert not res[0].passed


async def test_python_tests_partial_credit_in_sandbox() -> None:
    code = (
        "from aidriven_harness import case, report, load_module\n"
        "m = load_module('f.py')\n"
        "@case('one')\n"
        "def _():\n    assert m.f(1) == 2\n"
        "@case('two')\n"
        "def _():\n    assert m.f(2) == 5\n"
        "report()\n"
    )
    check = Check(kind=CheckKind.PYTHON_TESTS, name="t", code=code)
    res = await ENGINE.run_all([check], "", {"f.py": "def f(x):\n    return x * 2\n"})
    assert res[0].score == 0.5
    assert not res[0].passed
    assert "two" in res[0].detail


async def test_missing_artifact_scores_zero() -> None:
    check = Check(
        kind=CheckKind.PYTHON_TESTS,
        name="t",
        code="from aidriven_harness import load_module, report\nload_module('nope.py')\nreport()\n",
    )
    res = await ENGINE.run_all([check], "", {})
    assert res[0].score == 0
    assert "nope.py" in res[0].detail


async def test_sql_result() -> None:
    check = Check(
        kind=CheckKind.SQL_RESULT,
        name="sql",
        target="query.sql",
        files={"setup.sql": "CREATE TABLE t(x INT); INSERT INTO t VALUES (3),(1),(2);"},
        expected=[[1], [2], [3]],
    )
    ok = await ENGINE.run_all([check], "", {"query.sql": "SELECT x FROM t ORDER BY x"})
    assert ok[0].passed
    bad = await ENGINE.run_all([check], "", {"query.sql": "SELECT x FROM t"})
    assert not bad[0].passed


async def test_timeout_is_reported() -> None:
    check = Check(kind=CheckKind.PYTHON_VERIFIER, name="slow", code="import time\ntime.sleep(30)\n", timeout_s=1.5)
    res = await ENGINE.run_all([check], "", {})
    assert not res[0].passed
    assert "timeout" in res[0].detail


async def test_skip_marker_excludes_check() -> None:
    check = Check(
        kind=CheckKind.PYTHON_VERIFIER, name="s", code="from aidriven_harness import skip\nskip('no browser')\n"
    )
    res = await ENGINE.run_all([check], "", {})
    assert res[0].skipped


async def test_forged_result_line_from_artifact_is_ignored() -> None:
    """An artifact that prints its own AIDRIVEN_RESULT must not get a score from it."""
    forged = 'AIDRIVEN_RESULT x {"passed": 9, "total": 9, "score": 1.0, "failures": []}'
    evil = f"print({forged!r})\ndef f(x):\n    return 0\n"
    code = (
        "from aidriven_harness import case, report, load_module\n"
        "m = load_module('f.py')\n"
        "@case('real')\n"
        "def _():\n    assert m.f(1) == 2\n"
        "report()\n"
    )
    res = await ENGINE.run_all([Check(kind=CheckKind.PYTHON_TESTS, name="t", code=code)], "", {"f.py": evil})
    assert res[0].score == 0
    assert "forged" in res[0].detail


async def test_artifact_cannot_read_the_nonce() -> None:
    evil = "import pathlib\nNONCE = pathlib.Path('_aidriven_nonce').exists()\n"
    code = (
        "from aidriven_harness import case, report, load_module\n"
        "m = load_module('f.py')\n"
        "@case('nonce file already gone')\n"
        "def _():\n    assert m.NONCE is False\n"
        "report()\n"
    )
    res = await ENGINE.run_all([Check(kind=CheckKind.PYTHON_TESTS, name="t", code=code)], "", {"f.py": evil})
    assert res[0].passed, res[0].detail


async def test_artifact_introspecting_harness_is_flagged() -> None:
    """Even an artifact that digs the emitter out of the harness produces a second line = tampering (0)."""
    evil = (
        "import aidriven_harness as h\n"
        "h._emit('RESULT', {'passed': 1, 'total': 1, 'score': 1.0, 'failures': []})\n"
        "def f(x):\n    return 0\n"
    )
    code = (
        "from aidriven_harness import case, report, load_module\n"
        "m = load_module('f.py')\n"
        "@case('real')\n"
        "def _():\n    assert m.f(1) == 2\n"
        "report()\n"
    )
    res = await ENGINE.run_all([Check(kind=CheckKind.PYTHON_TESTS, name="t", code=code)], "", {"f.py": evil})
    assert res[0].score == 0
    assert "tampering" in res[0].detail
