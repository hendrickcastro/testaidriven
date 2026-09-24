"""OpenAI Responses API adapter. Also serves Azure OpenAI through its v1 endpoint (`<resource>/openai/v1/`).

Mapping: reasoning_effort -> reasoning.effort (+ summary "auto" to capture reasoning text),
verbosity -> text.verbosity, json_mode -> text.format json_object. `store=False`; for multi-turn tool loops
the encrypted reasoning items are requested and replayed verbatim.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import openai

from aidriven.adapters.llm.base import BaseAdapter, Stopwatch
from aidriven.domain.models import ProviderConnection, ProviderKind
from aidriven.ports import DiscoveredModel, LLMError, LLMRequest, LLMResponse, Message, ToolCall


def _azure_base_url(url: str) -> str:
    url = url.rstrip("/")
    return url + "/" if "/openai/" in url + "/" else url + "/openai/v1/"


class OpenAIResponsesAdapter(BaseAdapter):
    def __init__(self, connection: ProviderConnection, api_key: str | None) -> None:
        super().__init__(connection, api_key)
        kwargs: dict[str, Any] = {
            "timeout": connection.timeout_s,
            "max_retries": 0,
            "api_key": api_key or "missing",
        }
        if connection.kind == ProviderKind.AZURE_OPENAI:
            if not connection.base_url:
                raise LLMError("Azure OpenAI requires the resource endpoint as base URL")
            kwargs["base_url"] = _azure_base_url(connection.base_url)
        elif connection.base_url:
            kwargs["base_url"] = connection.base_url
        if connection.extra_headers:
            kwargs["default_headers"] = connection.extra_headers
        self.client = openai.AsyncOpenAI(**kwargs)

    def _is_transient(self, exc: Exception) -> bool:
        if isinstance(exc, (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError)):
            return True
        if isinstance(exc, openai.APIStatusError):
            return exc.status_code >= 500 or exc.status_code in (408, 409, 429)
        return False

    @staticmethod
    def _input_items(messages: list[Message]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                items.append({"type": "function_call_output", "call_id": m.tool_call_id, "output": m.text})
            elif m.role == "assistant":
                if m.provider_raw is not None:
                    items.extend(m.provider_raw)
                else:
                    if m.text:
                        items.append({"role": "assistant", "content": [{"type": "output_text", "text": m.text}]})
                    for c in m.tool_calls:
                        items.append(
                            {
                                "type": "function_call",
                                "call_id": c.id,
                                "name": c.name,
                                "arguments": json.dumps(c.arguments),
                            }
                        )
            else:
                content: list[dict[str, Any]] = [
                    {"type": "input_image", "image_url": f"data:{img.mime};base64,{img.data_b64}"} for img in m.images
                ]
                if m.text:
                    content.append({"type": "input_text", "text": m.text})
                items.append({"role": "user", "content": content})
        return items

    def build_kwargs(self, request: LLMRequest) -> dict[str, Any]:
        p = request.params
        kwargs: dict[str, Any] = {
            "model": request.model,
            "input": self._input_items(request.messages),
            "max_output_tokens": p.max_output_tokens,
            "store": False,
        }
        if request.system:
            kwargs["instructions"] = request.system
        if p.temperature is not None:
            kwargs["temperature"] = p.temperature
        if p.top_p is not None:
            kwargs["top_p"] = p.top_p
        if p.reasoning_effort is not None:
            kwargs["reasoning"] = {"effort": p.reasoning_effort, "summary": "auto"}
            kwargs["include"] = ["reasoning.encrypted_content"]
        text: dict[str, Any] = {}
        if p.verbosity is not None:
            text["verbosity"] = p.verbosity
        if p.json_mode:
            text["format"] = {"type": "json_object"}
        if text:
            kwargs["text"] = text
        if request.tools:
            kwargs["tools"] = [
                {"type": "function", "name": t.name, "description": t.description, "parameters": t.parameters}
                for t in request.tools
            ]
        if p.extra:
            kwargs["extra_body"] = dict(p.extra)
        return kwargs

    async def _generate(self, request: LLMRequest) -> LLMResponse:
        kwargs = self.build_kwargs(request)
        sw = Stopwatch()
        final: Any = None
        if request.params.stream:
            stream = await self.client.responses.create(stream=True, **kwargs)
            async for event in stream:
                etype = getattr(event, "type", "")
                if etype.endswith(".delta"):
                    sw.mark_first()
                elif etype in ("response.completed", "response.incomplete"):
                    final = event.response
                elif etype in ("response.failed", "error"):
                    err = getattr(getattr(event, "response", None), "error", None) or getattr(event, "message", event)
                    raise LLMError(f"OpenAI response failed: {err}")
        else:
            final = await self.client.responses.create(**kwargs)
        if final is None:
            raise LLMError("stream ended without a final response", transient=True)
        return self._to_response(final, sw)

    @staticmethod
    def _to_response(resp: Any, sw: Stopwatch) -> LLMResponse:
        texts, reasoning, calls, refusal = [], [], [], False
        replay: list[dict[str, Any]] = []
        for item in resp.output or []:
            itype = getattr(item, "type", "")
            replay.append(item.model_dump(exclude_none=True))
            if itype == "message":
                for part in item.content or []:
                    if part.type == "output_text":
                        texts.append(part.text)
                    elif part.type == "refusal":
                        refusal = True
                        texts.append(part.refusal)
            elif itype == "reasoning":
                for s in getattr(item, "summary", None) or []:
                    reasoning.append(getattr(s, "text", ""))
            elif itype == "function_call":
                try:
                    args = json.loads(item.arguments or "{}")
                except json.JSONDecodeError:
                    args = {"_raw": item.arguments}
                calls.append(ToolCall(id=item.call_id, name=item.name, arguments=args))
        u = resp.usage
        in_det = getattr(u, "input_tokens_details", None)
        out_det = getattr(u, "output_tokens_details", None)
        incomplete = getattr(resp, "incomplete_details", None)
        return LLMResponse(
            text="".join(texts),
            reasoning_text="\n".join(r for r in reasoning if r),
            tool_calls=calls,
            input_tokens=int(getattr(u, "input_tokens", 0) or 0),
            output_tokens=int(getattr(u, "output_tokens", 0) or 0),
            reasoning_tokens=int(getattr(out_det, "reasoning_tokens", 0) or 0),
            cached_tokens=int(getattr(in_det, "cached_tokens", 0) or 0),
            latency_ms=sw.elapsed_ms(),
            ttft_ms=sw.ttft_ms,
            finish_reason=(getattr(incomplete, "reason", None) if incomplete else getattr(resp, "status", None)),
            refusal=refusal,
            model_reported=getattr(resp, "model", None),
            raw_usage=u.model_dump() if u is not None else {},
            provider_raw=replay,
        )

    async def list_models(self) -> list[DiscoveredModel]:
        return [
            DiscoveredModel(id=m.id, label=m.id, created=getattr(m, "created", None))
            async for m in self.client.models.list()
        ]


class ChatCompletionsAdapter(BaseAdapter):
    """OpenAI-compatible Chat Completions: OpenRouter and any `openai_compatible` endpoint (vLLM, LM Studio...)."""

    DEFAULT_BASE: ClassVar[dict[ProviderKind, str]] = {ProviderKind.OPENROUTER: "https://openrouter.ai/api/v1"}

    def __init__(self, connection: ProviderConnection, api_key: str | None) -> None:
        super().__init__(connection, api_key)
        base = connection.base_url or self.DEFAULT_BASE.get(connection.kind)
        if not base:
            raise LLMError("an OpenAI-compatible provider requires a base URL")
        headers = dict(connection.extra_headers)
        if connection.kind == ProviderKind.OPENROUTER:
            headers.setdefault("X-Title", "aidriven")
        self.client = openai.AsyncOpenAI(
            api_key=api_key or "missing",
            base_url=base,
            timeout=connection.timeout_s,
            max_retries=0,
            default_headers=headers,
        )
        self._is_openrouter = connection.kind == ProviderKind.OPENROUTER

    _is_transient = OpenAIResponsesAdapter._is_transient

    @staticmethod
    def _messages(request: LLMRequest) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if request.system:
            out.append({"role": "system", "content": request.system})
        for m in request.messages:
            if m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.text})
            elif m.role == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.text or None}
                if m.tool_calls:
                    msg["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in m.tool_calls
                    ]
                out.append(msg)
            elif m.images:
                parts: list[dict[str, Any]] = [
                    {"type": "image_url", "image_url": {"url": f"data:{i.mime};base64,{i.data_b64}"}} for i in m.images
                ]
                parts.append({"type": "text", "text": m.text})
                out.append({"role": "user", "content": parts})
            else:
                out.append({"role": "user", "content": m.text})
        return out

    def build_kwargs(self, request: LLMRequest) -> dict[str, Any]:
        p = request.params
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": self._messages(request),
            "max_tokens": p.max_output_tokens,
        }
        extra: dict[str, Any] = dict(p.extra)
        for name in ("temperature", "top_p", "seed"):
            if getattr(p, name) is not None:
                kwargs[name] = getattr(p, name)
        if p.top_k is not None:
            extra["top_k"] = p.top_k
        if p.stop_sequences:
            kwargs["stop"] = p.stop_sequences
        if p.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if self._is_openrouter:
            reasoning: dict[str, Any] = {}
            if p.reasoning_effort is not None:
                reasoning["effort"] = p.reasoning_effort
            if p.reasoning_budget is not None:
                reasoning["max_tokens"] = p.reasoning_budget
            if p.reasoning_enabled is not None:
                reasoning["enabled"] = p.reasoning_enabled
            if reasoning:
                extra["reasoning"] = reasoning
            extra.setdefault("usage", {"include": True})
        elif p.reasoning_effort is not None:
            kwargs["reasoning_effort"] = p.reasoning_effort
        if p.verbosity is not None:
            kwargs["verbosity"] = p.verbosity
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in request.tools
            ]
        if extra:
            kwargs["extra_body"] = extra
        return kwargs

    async def _generate(self, request: LLMRequest) -> LLMResponse:
        kwargs = self.build_kwargs(request)
        sw = Stopwatch()
        # Tool calls are simpler and more reliable without streaming.
        if request.params.stream and not request.tools:
            return await self._stream(kwargs, sw)
        resp = await self.client.chat.completions.create(**kwargs)
        if not resp.choices:
            raise LLMError(f"empty completion: {getattr(resp, 'error', None) or resp}", transient=True)
        choice = resp.choices[0]
        msg = choice.message
        calls = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        out = LLMResponse(
            text=msg.content or "",
            reasoning_text=getattr(msg, "reasoning", None) or getattr(msg, "reasoning_content", None) or "",
            tool_calls=calls,
            latency_ms=sw.elapsed_ms(),
            finish_reason=choice.finish_reason,
            refusal=bool(getattr(msg, "refusal", None)),
            model_reported=resp.model,
        )
        self._usage(out, resp.usage)
        return out

    async def _stream(self, kwargs: dict[str, Any], sw: Stopwatch) -> LLMResponse:
        stream = await self.client.chat.completions.create(
            stream=True, stream_options={"include_usage": True}, **kwargs
        )
        text, reasoning, finish, model, usage = [], [], None, None, None
        async for chunk in stream:
            model = chunk.model or model
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            for choice in chunk.choices or []:
                delta = choice.delta
                piece = getattr(delta, "content", None)
                think = getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None)
                if piece or think:
                    sw.mark_first()
                if piece:
                    text.append(piece)
                if think:
                    reasoning.append(think)
                if choice.finish_reason:
                    finish = choice.finish_reason
        out = LLMResponse(
            text="".join(text),
            reasoning_text="".join(reasoning),
            latency_ms=sw.elapsed_ms(),
            ttft_ms=sw.ttft_ms,
            finish_reason=finish,
            model_reported=model,
        )
        self._usage(out, usage)
        return out

    @staticmethod
    def _usage(out: LLMResponse, usage: Any) -> None:
        if usage is None:
            return
        out.input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        out.output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        cdet = getattr(usage, "completion_tokens_details", None)
        pdet = getattr(usage, "prompt_tokens_details", None)
        out.reasoning_tokens = int(getattr(cdet, "reasoning_tokens", 0) or 0)
        out.cached_tokens = int(getattr(pdet, "cached_tokens", 0) or 0)
        out.raw_usage = usage.model_dump() if hasattr(usage, "model_dump") else {}

    async def list_models(self) -> list[DiscoveredModel]:
        out = []
        async for m in self.client.models.list():
            extra = m.model_extra or {}
            pricing = extra.get("pricing") or {}
            arch = extra.get("architecture") or {}
            top = extra.get("top_provider") or {}

            def per_mtok(v: Any) -> float | None:
                try:
                    return float(v) * 1e6
                except (TypeError, ValueError):
                    return None

            out.append(
                DiscoveredModel(
                    id=m.id,
                    label=extra.get("name") or m.id,
                    context_window=extra.get("context_length"),
                    max_output_tokens=top.get("max_completion_tokens"),
                    vision=("image" in (arch.get("input_modalities") or [])) if arch else None,
                    input_per_mtok=per_mtok(pricing.get("prompt")),
                    output_per_mtok=per_mtok(pricing.get("completion")),
                    created=getattr(m, "created", None),
                )
            )
        return out
