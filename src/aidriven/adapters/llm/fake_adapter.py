"""Deterministic fake provider for tests and zero-cost demos. Clearly labelled as fake in the UI.

Models:
- `echo`   : repeats the start of the prompt and answers `FINAL ANSWER: 42`.
- `oracle` : replays the reference solution of the task (`<base_url>/<task_slug>/`), i.e. a perfect model.
- `judge`  : returns a valid judge JSON verdict (score 7).
- `flaky`  : fails ~30% of calls with a transient error (seeded by prompt), to exercise reliability metrics.
- `tools`  : calls `run_python` once, then answers with the tool output.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from aidriven.adapters.llm.base import BaseAdapter, Stopwatch
from aidriven.domain.models import ProviderConnection
from aidriven.ports import DiscoveredModel, LLMError, LLMRequest, LLMResponse, ToolCall

FAKE_MODELS = ["echo", "oracle", "judge", "flaky", "tools"]
DEFAULT_ORACLE_DIR = Path("tests/fixtures/seed_solutions")
_LANG = {
    ".py": "python",
    ".html": "html",
    ".js": "javascript",
    ".json": "json",
    ".sql": "sql",
    ".md": "markdown",
}


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def oracle_response(slug: str, root: Path) -> str | None:
    folder = root / slug
    if not folder.is_dir():
        return None
    resp = folder / "response.txt"
    if resp.exists():
        return resp.read_text(encoding="utf-8")
    blocks = []
    for f in sorted(folder.iterdir()):
        if f.is_file():
            lang = _LANG.get(f.suffix, "text")
            blocks.append(f"```{lang} file={f.name}\n{f.read_text(encoding='utf-8')}\n```")
    return "Here are the files.\n\n" + "\n\n".join(blocks) if blocks else None


class FakeAdapter(BaseAdapter):
    def __init__(self, connection: ProviderConnection, api_key: str | None) -> None:
        super().__init__(connection, api_key)
        self._attempts: dict[str, int] = {}

    def _is_transient(self, exc: Exception) -> bool:
        return isinstance(exc, LLMError) and exc.transient

    async def _generate(self, request: LLMRequest) -> LLMResponse:
        sw = Stopwatch()
        await asyncio.sleep(0.01)
        sw.mark_first()
        prompt = "\n".join(m.text for m in request.messages)
        model = request.model
        calls: list[ToolCall] = []
        if model == "oracle":
            root = Path(self.connection.base_url) if self.connection.base_url else DEFAULT_ORACLE_DIR
            text = oracle_response(str(request.metadata.get("task_slug", "")), root) or "No reference.\nFINAL ANSWER: ?"
        elif model == "judge":
            text = json.dumps(
                {
                    "score": 7,
                    "criteria": [{"name": "correctness", "score": 7, "comment": "fake judge"}],
                    "rationale": "Deterministic fake verdict.",
                }
            )
        elif model == "flaky":
            # ~30% of prompts fail transiently on their first attempt; the retry succeeds.
            key = hashlib.sha256((prompt + str(request.metadata.get("repetition", ""))).encode()).hexdigest()
            attempt = self._attempts.get(key, 0)
            self._attempts[key] = attempt + 1
            if int(key, 16) % 10 < 3 and attempt == 0:
                raise LLMError("fake transient failure", transient=True, status=503)
            text = "Flaky but fine.\nFINAL ANSWER: 42"
        elif model == "tools" and request.tools and not any(m.role == "tool" for m in request.messages):
            text = "Let me compute that."
            calls = [ToolCall(id="call_fake_1", name="run_python", arguments={"code": "print(6*7)"})]
        elif model == "tools":
            last_tool = next((m.text for m in reversed(request.messages) if m.role == "tool"), "")
            text = f"The tool said: {last_tool.strip()[:200]}\nFINAL ANSWER: 42"
        else:
            text = f"Echo: {prompt[:120]}\nFINAL ANSWER: 42"
        return LLMResponse(
            text=text,
            tool_calls=calls,
            input_tokens=_tokens(request.system + prompt),
            output_tokens=_tokens(text),
            latency_ms=sw.elapsed_ms(),
            ttft_ms=sw.ttft_ms,
            finish_reason="tool_use" if calls else "stop",
            model_reported=f"fake/{model}",
        )

    async def list_models(self) -> list[DiscoveredModel]:
        return [DiscoveredModel(id=m, label=f"Fake {m}") for m in FAKE_MODELS]
