"""Builds the LLM request for a task: system prompt (profile + rulesets + agent persona), artifact
instructions, attachments (images for vision, text documents inline). Prompt ids: specs/05-llm/prompts.md.
"""

from __future__ import annotations

import base64
from importlib import resources
from pathlib import Path

from aidriven.domain.models import AgentDef, Attachment, ModelProfile, Ruleset, Task
from aidriven.ports import ImagePart, LLMRequest, Message
from aidriven.services.artifacts import LANG_EXT

_EXT_LANG = {v: k for k, v in reversed(list(LANG_EXT.items()))}

# prompt id: runner.artifact_instructions
ARTIFACT_INSTRUCTIONS = (
    "\n\n---\nDelivery format (mandatory): return every requested file in its own fenced code block whose opening "
    "line is exactly ```<language> file=<name> — for example ```python file=solver.py. Requested files: {names}. "
    "Each block must contain the complete file content."
)


def assets_dir() -> Path:
    return Path(str(resources.files("aidriven") / "seed" / "assets"))


def attachment_bytes(att: Attachment) -> bytes:
    if att.data_b64:
        return base64.b64decode(att.data_b64)
    if att.path:
        path = assets_dir() / att.path
        return path.read_bytes()
    return b""


def compose_system(profile: ModelProfile, rulesets: list[Ruleset], agent: AgentDef | None) -> str:
    before = [r.content.strip() for r in rulesets if r.position == "prepend"]
    after = [r.content.strip() for r in rulesets if r.position == "append"]
    parts = [*before]
    if agent:
        parts.append(agent.persona.strip())
    if profile.system_prompt.strip():
        parts.append(profile.system_prompt.strip())
    parts.extend(after)
    return "\n\n".join(p for p in parts if p)


def compose_user_message(task: Task, vision: bool) -> Message:
    text = task.prompt.rstrip()
    docs: list[str] = []
    images: list[ImagePart] = []
    for att in task.attachments:
        if att.mime.startswith("image/"):
            if vision:
                images.append(ImagePart(mime=att.mime, data_b64=base64.b64encode(attachment_bytes(att)).decode()))
        elif att.inline_text or att.mime.startswith("text/") or att.mime == "application/json":
            content = attachment_bytes(att).decode("utf-8", errors="replace")
            docs.append(f'<document name="{att.name}">\n{content}\n</document>')
    if docs:
        text = "\n\n".join(docs) + "\n\n" + text
    if task.artifact_names:
        text += ARTIFACT_INSTRUCTIONS.format(names=", ".join(task.artifact_names))
    return Message(role="user", text=text, images=images)


def build_request(
    task: Task,
    profile: ModelProfile,
    rulesets: list[Ruleset],
    agent: AgentDef | None = None,
    metadata: dict[str, object] | None = None,
) -> LLMRequest:
    return LLMRequest(
        model=profile.model,
        messages=[compose_user_message(task, profile.capabilities.vision)],
        params=profile.params.model_copy(deep=True),
        system=compose_system(profile, rulesets, agent),
        metadata={"task_slug": task.slug, **(metadata or {})},
    )


def missing_capabilities(task: Task, profile: ModelProfile) -> list[str]:
    caps = profile.capabilities
    missing = []
    if "vision" in task.requires and not caps.vision:
        missing.append("vision")
    if "tools" in task.requires and not caps.tools:
        missing.append("tools")
    if "json_mode" in task.requires and not caps.json_mode:
        missing.append("json_mode")
    return missing
