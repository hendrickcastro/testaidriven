"""Check engine: evaluates a task's checks against a model response and its extracted artifacts.

In-process kinds (exact, numeric, regex, json_schema) are pure functions. Sandbox kinds (python_tests,
python_verifier, sql_result, html_playwright) run in the SandboxPort with the stdlib harness
(specs/03-modules/evaluation.md). Nothing the model wrote is executed in-process.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from importlib import resources
from typing import Any

import jsonschema

from aidriven.domain.models import Check, CheckKind, CheckResult
from aidriven.ports import SandboxJob, SandboxPort

log = logging.getLogger(__name__)

FINAL_RE = re.compile(r"(?im)^\s*\**\s*FINAL ANSWER\s*\**\s*:\s*\**\s*(.+?)\s*\**\s*$")
_JSON_BLOCK_RE = re.compile(r"```(?:json)?[^\n]*\n(.*?)```", re.S)
_RESULT_RE = re.compile(r"^AIDRIVEN_RESULT (\S+) (\{.*\})\s*$", re.M)
_SKIP_RE = re.compile(r"^AIDRIVEN_SKIP (\S+) (\{.*\})\s*$", re.M)
NONCE_FILE = "_aidriven_nonce"
_NUM_RE = re.compile(r"[-+]?(?:\d[\d,_]*\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")

SQL_RUNNER = r"""
import json, sqlite3
from aidriven_harness import case, report, read, exists

EXPECTED = json.loads(read("_expected.json"))
TARGET = {target!r}

@case("query returns the expected rows", timeout={timeout})
def _():
    assert exists(TARGET), f"the model did not deliver {{TARGET}}"
    conn = sqlite3.connect(":memory:")
    if exists("setup.sql"):
        conn.executescript(read("setup.sql"))
    rows = [list(r) for r in conn.execute(read(TARGET)).fetchall()]
    assert rows == EXPECTED, (
        f"got {{rows[:5]}}... ({{len(rows)}} rows), expected {{EXPECTED[:5]}}... ({{len(EXPECTED)}} rows)"
    )

report()
"""


def harness_source() -> bytes:
    return (resources.files("aidriven") / "sandbox_harness" / "aidriven_harness.py").read_bytes()


def extract_final_answer(text: str, pattern: str | None = None) -> str | None:
    if pattern:
        matches = list(re.finditer(pattern, text))
        if not matches:
            return None
        m = matches[-1]
        return (m.group(1) if m.groups() else m.group(0)).strip()
    found = FINAL_RE.findall(text)
    return found[-1].strip().strip("`").strip() if found else None


def normalize(value: Any, case_sensitive: bool = False) -> str:
    s = re.sub(r"\s+", " ", str(value)).strip().rstrip(".")
    return s if case_sensitive else s.casefold()


def parse_number(text: str) -> float | None:
    m = _NUM_RE.findall(text.replace("−", "-"))
    if not m:
        return None
    try:
        return float(m[-1].replace(",", "").replace("_", ""))
    except ValueError:
        return None


class CheckEngine:
    def __init__(self, sandbox: SandboxPort) -> None:
        self.sandbox = sandbox

    async def run_all(self, checks: list[Check], response_text: str, files: dict[str, str]) -> list[CheckResult]:
        results = []
        for check in checks:
            t0 = time.perf_counter()
            try:
                res = await self.run_one(check, response_text, files)
            except Exception as exc:  # a broken check must not break the run
                log.exception("check %s crashed", check.name)
                res = self._result(check, False, 0.0, f"check crashed: {exc}")
            res.duration_ms = (time.perf_counter() - t0) * 1000
            results.append(res)
        return results

    @staticmethod
    def _result(check: Check, passed: bool, score: float, detail: str, skipped: bool = False) -> CheckResult:
        return CheckResult(
            check_id=check.id,
            name=check.name,
            dimension=check.dimension,
            passed=passed,
            score=max(0.0, min(1.0, score)),
            weight=check.weight,
            detail=detail[:4000],
            skipped=skipped,
        )

    def _target_text(self, check: Check, response_text: str, files: dict[str, str]) -> str | None:
        if check.target:
            return files.get(check.target)
        return response_text

    async def run_one(self, check: Check, response_text: str, files: dict[str, str]) -> CheckResult:
        kind = check.kind
        if kind == CheckKind.EXACT:
            got = extract_final_answer(response_text, check.pattern)
            if got is None:
                return self._result(check, False, 0, "no final answer found")
            ok = normalize(got, check.case_sensitive) == normalize(check.expected, check.case_sensitive)
            return self._result(check, ok, float(ok), f"got {got!r}, expected {check.expected!r}")
        if kind == CheckKind.NUMERIC:
            got = extract_final_answer(response_text, check.pattern)
            num = parse_number(got) if got is not None else None
            if num is None:
                return self._result(check, False, 0, f"no numeric final answer (got {got!r})")
            ok = abs(num - float(check.expected)) <= check.tolerance
            return self._result(check, ok, float(ok), f"got {num}, expected {check.expected} ± {check.tolerance}")
        if kind == CheckKind.REGEX:
            text = self._target_text(check, response_text, files)
            if text is None:
                return self._result(check, False, 0, f"artifact {check.target} missing")
            ok = re.search(check.pattern or "", text) is not None
            return self._result(check, ok, float(ok), f"pattern {'found' if ok else 'not found'}: {check.pattern}")
        if kind == CheckKind.JSON_SCHEMA:
            return self._json_schema(check, response_text, files)
        if kind in (
            CheckKind.PYTHON_TESTS,
            CheckKind.PYTHON_VERIFIER,
            CheckKind.HTML_PLAYWRIGHT,
            CheckKind.SQL_RESULT,
        ):
            return await self._sandboxed(check, response_text, files)
        return self._result(check, False, 0, f"unknown check kind {kind}")

    def _json_schema(self, check: Check, response_text: str, files: dict[str, str]) -> CheckResult:
        if check.target:
            raw = files.get(check.target)
            if raw is None:
                return self._result(check, False, 0, f"artifact {check.target} missing")
        else:
            blocks = _JSON_BLOCK_RE.findall(response_text)
            raw = blocks[-1] if blocks else response_text
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return self._result(check, False, 0, f"invalid JSON: {exc}")
        errors = sorted(jsonschema.Draft202012Validator(check.schema_ or {}).iter_errors(data), key=lambda e: e.path)
        if errors:
            return self._result(check, False, 0, "; ".join(e.message for e in errors[:5]))
        return self._result(check, True, 1.0, "valid against schema")

    async def _sandboxed(self, check: Check, response_text: str, files: dict[str, str]) -> CheckResult:
        job_files: dict[str, bytes] = {name: content.encode("utf-8") for name, content in files.items()}
        job_files.update({name: content.encode("utf-8") for name, content in check.files.items()})
        job_files["response.txt"] = response_text.encode("utf-8")
        job_files["aidriven_harness.py"] = harness_source()
        if check.kind == CheckKind.SQL_RESULT:
            target = check.target or "query.sql"
            code = SQL_RUNNER.format(target=target, timeout=max(1.0, check.timeout_s - 5))
            job_files["_expected.json"] = json.dumps(check.expected).encode()
        else:
            code = check.code or "raise SystemExit('check has no code')"
        job_files["_check.py"] = code.encode("utf-8")
        # Single-use nonce: the harness reads and deletes it on import, before any model code is loaded, so an
        # artifact cannot print a valid result line by itself. Lines without it are forgeries and are ignored.
        nonce = secrets.token_hex(16)
        job_files[NONCE_FILE] = nonce.encode()
        record = await self.sandbox.run(
            SandboxJob(
                files=job_files,
                command=["python", "_check.py"],
                timeout_s=check.timeout_s,
                needs_browser=check.kind == CheckKind.HTML_PLAYWRIGHT,
            )
        )
        results = [m for m in _RESULT_RE.finditer(record.stdout) if m.group(1) == nonce]
        skips = [m for m in _SKIP_RE.finditer(record.stdout) if m.group(1) == nonce]
        forged = (
            len(_RESULT_RE.findall(record.stdout)) + len(_SKIP_RE.findall(record.stdout)) - len(results) - len(skips)
        )
        if len(results) + len(skips) > 1:
            return self._result(check, False, 0, "tampering: more than one authenticated result line (0 points)")
        note = f"\n(ignored {forged} forged result line(s) printed by the artifact)" if forged else ""
        if skips:
            reason = json.loads(skips[0].group(2)).get("reason", "skipped")
            return self._result(check, False, 0, f"skipped: {reason}{note}", skipped=True)
        if record.timed_out:
            return self._result(check, False, 0, f"timeout after {check.timeout_s}s ({record.isolation}){note}")
        if not results:
            tail = (record.stderr or record.stdout)[-1500:]
            return self._result(
                check, False, 0, f"no result (exit {record.exit_code}, {record.isolation}):{note}\n{tail}"
            )
        data = json.loads(results[0].group(2))
        score = float(data.get("score", 0.0))
        failures = data.get("failures") or []
        detail = f"{data.get('passed')}/{data.get('total')} cases passed ({record.isolation})"
        detail += note
        if failures:
            detail += "\n" + "\n".join(f"- {f['case']}: {f['error']}" for f in failures[:10])
        return self._result(check, score >= 0.999, score, detail)
