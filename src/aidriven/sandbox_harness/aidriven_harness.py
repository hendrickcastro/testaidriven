"""Minimal (stdlib-only) harness copied into the sandbox working directory.

Check scripts (`python_tests`, `python_verifier`, `sql_result`) use it like this:

    from aidriven_harness import case, report, response_text, final_answer, load_module

    solver = load_module("solver.py")

    @case("solves the trivial case")
    def _():
        assert solver.solve([]) == []

    report()

`report()` prints an `AIDRIVEN_RESULT <nonce> {json}` line with passed/total/failures, which the check engine turns
into partial credit (passed weight / total weight). Crashing before `report()` = 0 points.
Works the same in Docker and in the subprocess fallback: it depends on nothing outside the stdlib.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

WORKDIR = Path(__file__).resolve().parent


def _take_nonce() -> str:
    """Read and delete the engine's single-use nonce before any model code runs (see checks.py)."""
    path = WORKDIR / "_aidriven_nonce"
    try:
        value = path.read_text(encoding="utf-8").strip()
        path.unlink()
    except OSError:
        value = "-"
    return value


def _make_emitter(nonce: str) -> Callable[[str, dict[str, Any]], None]:
    """The nonce lives only in this closure (not a module attribute), and lines go straight to fd 1 so a
    monkeypatched print/sys.stdout cannot swallow or rewrite them. A second authenticated line = tampering."""

    def emit(kind: str, payload: dict[str, Any]) -> None:
        sys.stdout.flush()
        line = f"AIDRIVEN_{kind} {nonce} {json.dumps(payload, ensure_ascii=False)}" + chr(10)
        os.write(1, line.encode("utf-8"))

    return emit


_emit = _make_emitter(_take_nonce())
_CASES: list[tuple[str, Callable[[], Any], float, float]] = []
_FINAL_RE = re.compile(r"(?im)^\s*\**\s*FINAL ANSWER\s*\**\s*:\s*\**\s*(.+?)\s*\**\s*$")


def case(name: str, weight: float = 1.0, timeout: float = 10.0) -> Callable[[Callable[[], Any]], Callable[[], Any]]:
    """Register a test case. `timeout` per case (seconds); runs on a watched thread.

    Use timeout=0 to run on the main thread (required by sync playwright, which is not thread-safe).
    """

    def deco(fn: Callable[[], Any]) -> Callable[[], Any]:
        _CASES.append((name, fn, weight, timeout))
        return fn

    return deco


def response_text() -> str:
    """Full response of the evaluated model."""
    p = WORKDIR / "response.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def final_answer() -> str | None:
    """Last `FINAL ANSWER: ...` line of the response (or None)."""
    matches = _FINAL_RE.findall(response_text())
    return matches[-1].strip().strip("`").strip() if matches else None


def read(name: str) -> str:
    return (WORKDIR / name).read_text(encoding="utf-8")


def exists(name: str) -> bool:
    return (WORKDIR / name).exists()


def load_module(filename: str, module_name: str | None = None) -> ModuleType:
    """Import a python artifact by file name. Raises if missing or if it does not compile."""
    path = WORKDIR / filename
    if not path.exists():
        raise FileNotFoundError(f"the model did not deliver {filename}")
    name = module_name or path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def skip(reason: str) -> None:
    """Mark the check as not evaluable in this environment (e.g. playwright missing). Neither scores nor penalizes."""
    _emit("SKIP", {"reason": reason})
    sys.exit(0)


class _Page:
    """Context manager: opens an HTML artifact in headless Chromium (playwright) with no network."""

    def __init__(self, filename: str, width: int = 1024, height: int = 768) -> None:
        self.filename, self.width, self.height = filename, width, height

    def __enter__(self) -> Any:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            skip("playwright is not available in this sandbox")
        path = WORKDIR / self.filename
        if not path.exists():
            raise FileNotFoundError(f"the model did not deliver {self.filename}")
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(args=["--no-sandbox"])
        ctx = self._browser.new_context(viewport={"width": self.width, "height": self.height})
        ctx.route("http*://**", lambda route: route.abort())  # no network: the artifact must be self-contained
        self.page = ctx.new_page()
        self.errors: list[str] = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.goto(path.as_uri())
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            self.page.screenshot(path=str(WORKDIR / "_screenshot.png"))
        except Exception:
            pass
        self._browser.close()
        self._pw.stop()


def open_page(filename: str, width: int = 1024, height: int = 768) -> _Page:
    return _Page(filename, width, height)


def _format_exc(exc: BaseException) -> str:
    if isinstance(exc, AssertionError):
        return f"AssertionError: {exc}" if str(exc) else "AssertionError"
    return "".join(traceback.format_exception_only(type(exc), exc)).strip()[-800:]


def _run_with_timeout(fn: Callable[[], Any], timeout: float) -> tuple[bool, str]:
    if timeout <= 0:
        try:
            fn()
            return True, ""
        except Exception as exc:
            return False, _format_exc(exc)

    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            fn()
            outcome["ok"] = True
        except BaseException as exc:
            outcome["err"] = _format_exc(exc)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return False, f"timeout > {timeout}s"
    if outcome.get("ok"):
        return True, ""
    return False, outcome.get("err", "unknown error")


def report() -> None:
    passed_w = 0.0
    total_w = 0.0
    passed = 0
    failures: list[dict[str, str]] = []
    for name, fn, weight, timeout in _CASES:
        total_w += weight
        t0 = time.perf_counter()
        ok, err = _run_with_timeout(fn, timeout)
        ms = (time.perf_counter() - t0) * 1000
        if ok:
            passed += 1
            passed_w += weight
        else:
            failures.append({"case": name, "error": err, "ms": f"{ms:.0f}"})
    result = {
        "passed": passed,
        "total": len(_CASES),
        "score": (passed_w / total_w) if total_w else 0.0,
        "failures": failures[:30],
    }
    sys.stdout.flush()
    _emit("RESULT", result)
