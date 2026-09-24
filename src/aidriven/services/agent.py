"""Agentic loop: the model can call sandbox tools for up to `max_turns` (prompt id `agent.loop`).

Every tool runs in the SandboxPort; files written with `write_file` become artifacts of the result.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from aidriven.adapters.llm.base import BaseAdapter
from aidriven.domain.models import AgentDef, AgentTool, Task
from aidriven.ports import LLMRequest, LLMResponse, Message, SandboxJob, SandboxPort, ToolCall, ToolSpec
from aidriven.services.prompting import attachment_bytes

log = logging.getLogger(__name__)
TOOL_OUTPUT_LIMIT = 8000

TOOL_SPECS: dict[AgentTool, ToolSpec] = {
    AgentTool.RUN_PYTHON: ToolSpec(
        name="run_python",
        description=(
            "Run a Python 3 script (standard library only, no network) in an isolated sandbox. Files previously "
            "saved with write_file are available in the working directory. Returns exit code, stdout and stderr."
        ),
        parameters={
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python source to execute"}},
            "required": ["code"],
            "additionalProperties": False,
        },
    ),
    AgentTool.WRITE_FILE: ToolSpec(
        name="write_file",
        description="Save a file in the working directory (it becomes a deliverable artifact). Overwrites.",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}, "content": {"type": "string"}},
            "required": ["name", "content"],
            "additionalProperties": False,
        },
    ),
    AgentTool.RUN_HTML: ToolSpec(
        name="run_html",
        description=(
            "Open a saved HTML file in a headless browser (no network) and return console errors and the visible "
            "text of the page after load."
        ),
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "HTML file saved with write_file"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    ),
    AgentTool.READ_ATTACHMENT: ToolSpec(
        name="read_attachment",
        description="Read a text attachment of the task by name.",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    ),
}

HTML_PROBE = """
import json
from aidriven_harness import open_page
with open_page(NAME) as p:
    p.page.wait_for_timeout(500)
    text = p.page.inner_text("body")[:4000]
    print(json.dumps({"errors": p.errors[:20], "text": text}))
"""


@dataclass
class AgentOutcome:
    final_text: str
    responses: list[LLMResponse]
    transcript: list[dict[str, Any]]
    files: dict[str, str] = field(default_factory=dict)
    tool_calls: int = 0


class AgentRunner:
    def __init__(self, adapter: BaseAdapter, sandbox: SandboxPort, agent: AgentDef, task: Task) -> None:
        self.adapter = adapter
        self.sandbox = sandbox
        self.agent = agent
        self.task = task
        self.files: dict[str, str] = {}

    def tools(self) -> list[ToolSpec]:
        return [TOOL_SPECS[t] for t in self.agent.tools if t in TOOL_SPECS]

    async def run(self, request: LLMRequest) -> AgentOutcome:
        request.tools = self.tools()
        messages = list(request.messages)
        responses: list[LLMResponse] = []
        transcript: list[dict[str, Any]] = []
        n_calls = 0
        final_text = ""
        for turn in range(self.agent.max_turns):
            request.messages = messages
            resp = await self.adapter.generate(request)
            responses.append(resp)
            transcript.append(
                {
                    "turn": turn,
                    "role": "assistant",
                    "text": resp.text,
                    "tool_calls": [c.__dict__ for c in resp.tool_calls],
                }
            )
            final_text = resp.text or final_text
            if not resp.tool_calls or resp.refusal:
                break
            messages.append(
                Message(
                    role="assistant",
                    text=resp.text,
                    tool_calls=resp.tool_calls,
                    provider_raw=resp.provider_raw,
                )
            )
            for call in resp.tool_calls:
                n_calls += 1
                output = await self.execute(call)
                transcript.append({"turn": turn, "role": "tool", "name": call.name, "output": output[:2000]})
                messages.append(Message(role="tool", text=output, tool_call_id=call.id, tool_name=call.name))
        else:
            final_text += "\n\n[agent stopped: max_turns reached]"
        return AgentOutcome(
            final_text=final_text,
            responses=responses,
            transcript=transcript,
            files=dict(self.files),
            tool_calls=n_calls,
        )

    async def execute(self, call: ToolCall) -> str:
        args = call.arguments or {}
        try:
            if call.name == "write_file":
                name, content = str(args["name"]), str(args["content"])
                self.files[name] = content
                return f"saved {name} ({len(content)} chars)"
            if call.name == "read_attachment":
                att = next((a for a in self.task.attachments if a.name == args.get("name")), None)
                if att is None:
                    available = [a.name for a in self.task.attachments]
                    return f"no attachment named {args.get('name')!r}; available: {available}"
                if att.mime.startswith("image/"):
                    return "this attachment is an image; it was provided in the first message"
                return attachment_bytes(att).decode("utf-8", errors="replace")[:TOOL_OUTPUT_LIMIT]
            if call.name == "run_python":
                return await self._run(["python", "_main.py"], {"_main.py": str(args.get("code", ""))}, browser=False)
            if call.name == "run_html":
                from aidriven.services.checks import harness_source

                code = f"NAME = {str(args.get('name', 'index.html'))!r}\n" + HTML_PROBE
                return await self._run(
                    ["python", "_probe.py"],
                    {"_probe.py": code, "aidriven_harness.py": harness_source().decode()},
                    browser=True,
                )
            return f"unknown tool {call.name}"
        except Exception as exc:
            return f"tool error: {type(exc).__name__}: {exc}"

    async def _run(self, command: list[str], extra: dict[str, str], browser: bool) -> str:
        files = {n: c.encode("utf-8") for n, c in {**self.files, **extra}.items()}
        rec = await self.sandbox.run(
            SandboxJob(files=files, command=command, timeout_s=self.agent.tool_timeout_s, needs_browser=browser)
        )
        out = {
            "exit_code": rec.exit_code,
            "timed_out": rec.timed_out,
            "stdout": rec.stdout[-TOOL_OUTPUT_LIMIT:],
            "stderr": rec.stderr[-3000:],
        }
        return json.dumps(out, ensure_ascii=False)
