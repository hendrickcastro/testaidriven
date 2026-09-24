"""Google Gemini adapter (official `google-genai` SDK, async).

Mapping: reasoning_budget -> thinking_config.thinking_budget (Gemini 2.5), reasoning_effort ->
thinking_config.thinking_level (Gemini 3), include_thoughts=True to capture reasoning summaries.
Automatic function calling is disabled: the agent loop executes tools itself (in the sandbox).
`output_tokens` = candidates + thoughts (both billed as output).
"""

from __future__ import annotations

import base64
from typing import Any

from aidriven.adapters.llm.base import BaseAdapter, Stopwatch
from aidriven.domain.models import ProviderConnection
from aidriven.ports import DiscoveredModel, LLMError, LLMRequest, LLMResponse, Message, ToolCall


class GeminiAdapter(BaseAdapter):
    def __init__(self, connection: ProviderConnection, api_key: str | None) -> None:
        super().__init__(connection, api_key)
        from google import genai
        from google.genai import types

        self.types = types
        http_options: dict[str, Any] = {"timeout": int(connection.timeout_s * 1000)}
        if connection.base_url:
            http_options["base_url"] = connection.base_url
        if connection.extra_headers:
            http_options["headers"] = connection.extra_headers
        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(**http_options))

    def _is_transient(self, exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        if isinstance(code, int):
            return code in (408, 429) or code >= 500
        return super()._is_transient(exc)

    def _contents(self, messages: list[Message]) -> list[Any]:
        t = self.types
        contents: list[Any] = []
        tool_turn: Any = None  # consecutive tool results are grouped into one user turn
        for m in messages:
            if m.role != "tool":
                tool_turn = None
            if m.role == "assistant":
                if m.provider_raw is not None:
                    contents.append(m.provider_raw)
                    continue
                parts = [t.Part.from_text(text=m.text)] if m.text else []
                for c in m.tool_calls:
                    parts.append(t.Part.from_function_call(name=c.name, args=c.arguments))
                contents.append(t.Content(role="model", parts=parts))
            elif m.role == "tool":
                part = t.Part.from_function_response(name=m.tool_name or "tool", response={"result": m.text})
                if tool_turn is not None:
                    tool_turn.parts.append(part)
                else:
                    tool_turn = t.Content(role="user", parts=[part])
                    contents.append(tool_turn)
            else:
                parts = [t.Part.from_bytes(data=base64.b64decode(i.data_b64), mime_type=i.mime) for i in m.images]
                if m.text:
                    parts.append(t.Part.from_text(text=m.text))
                contents.append(t.Content(role="user", parts=parts))
        return contents

    def build_config(self, request: LLMRequest) -> Any:
        t, p = self.types, request.params
        cfg: dict[str, Any] = {"max_output_tokens": p.max_output_tokens}
        if request.system:
            cfg["system_instruction"] = request.system
        for name in ("temperature", "top_p", "top_k", "seed"):
            if getattr(p, name) is not None:
                cfg[name] = getattr(p, name)
        if p.stop_sequences:
            cfg["stop_sequences"] = p.stop_sequences
        if p.json_mode:
            cfg["response_mime_type"] = "application/json"
        thinking: dict[str, Any] = {}
        if p.reasoning_budget is not None:
            thinking["thinking_budget"] = p.reasoning_budget
        if p.reasoning_effort is not None:
            thinking["thinking_level"] = p.reasoning_effort
        if p.reasoning_enabled is False:
            thinking["thinking_budget"] = 0
        if thinking or p.reasoning_enabled:
            thinking["include_thoughts"] = True
            cfg["thinking_config"] = t.ThinkingConfig(**thinking)
        if request.tools:
            cfg["tools"] = [
                t.Tool(
                    function_declarations=[
                        t.FunctionDeclaration(
                            name=x.name, description=x.description, parameters_json_schema=x.parameters
                        )
                        for x in request.tools
                    ]
                )
            ]
            cfg["automatic_function_calling"] = t.AutomaticFunctionCallingConfig(disable=True)
        cfg.update(p.extra)
        return t.GenerateContentConfig(**cfg)

    async def _generate(self, request: LLMRequest) -> LLMResponse:
        contents = self._contents(request.messages)
        config = self.build_config(request)
        sw = Stopwatch()
        texts: list[str] = []
        thoughts: list[str] = []
        calls: list[ToolCall] = []
        parts_all: list[Any] = []
        usage, finish, model, blocked = None, None, None, None
        chunks: list[Any]
        if request.params.stream and not request.tools:
            chunks = []
            async for chunk in await self.client.aio.models.generate_content_stream(
                model=request.model, contents=contents, config=config
            ):
                sw.mark_first()
                chunks.append(chunk)
        else:
            chunks = [
                await self.client.aio.models.generate_content(model=request.model, contents=contents, config=config)
            ]
        for chunk in chunks:
            usage = chunk.usage_metadata or usage
            model = getattr(chunk, "model_version", None) or model
            fb = getattr(chunk, "prompt_feedback", None)
            if fb is not None and getattr(fb, "block_reason", None):
                blocked = str(fb.block_reason)
            for cand in chunk.candidates or []:
                if cand.finish_reason:
                    finish = str(getattr(cand.finish_reason, "name", cand.finish_reason))
                for part in (cand.content.parts if cand.content else None) or []:
                    parts_all.append(part)
                    if getattr(part, "function_call", None):
                        fc = part.function_call
                        calls.append(
                            ToolCall(id=fc.id or f"call_{len(calls)}", name=fc.name, arguments=dict(fc.args or {}))
                        )
                    elif part.text:
                        (thoughts if getattr(part, "thought", False) else texts).append(part.text)
        if not chunks:
            raise LLMError("Gemini returned no content", transient=True)
        cand_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
        thought_tokens = int(getattr(usage, "thoughts_token_count", 0) or 0)
        return LLMResponse(
            text="".join(texts),
            reasoning_text="".join(thoughts),
            tool_calls=calls,
            input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
            output_tokens=cand_tokens + thought_tokens,
            reasoning_tokens=thought_tokens,
            cached_tokens=int(getattr(usage, "cached_content_token_count", 0) or 0),
            latency_ms=sw.elapsed_ms(),
            ttft_ms=sw.ttft_ms,
            finish_reason=blocked or finish,
            refusal=bool(blocked) or finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"),
            model_reported=model,
            raw_usage=usage.model_dump(exclude_none=True) if usage is not None else {},
            provider_raw=self.types.Content(role="model", parts=parts_all) if parts_all else None,
        )

    async def list_models(self) -> list[DiscoveredModel]:
        out = []
        async for m in await self.client.aio.models.list():
            name = (m.name or "").removeprefix("models/")
            actions = getattr(m, "supported_actions", None) or []
            if actions and "generateContent" not in actions:
                continue
            out.append(
                DiscoveredModel(
                    id=name,
                    label=m.display_name or name,
                    context_window=getattr(m, "input_token_limit", None),
                    max_output_tokens=getattr(m, "output_token_limit", None),
                )
            )
        return out
