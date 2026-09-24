"""Anthropic Messages API adapter (official `anthropic` SDK, async + streaming).

Parameter mapping (specs/05-llm/llm-port.md):
- ReasoningMode.EFFORT  -> thinking {"type": "adaptive", "display": "summarized"} + output_config.effort
  (`reasoning_enabled=False` sends thinking {"type": "disabled"} where the model accepts it).
- ReasoningMode.BUDGET  -> thinking {"type": "enabled", "budget_tokens": N} (older models, e.g. Haiku 4.5).
- Sampling params (temperature/top_p/top_k) are only sent when the profile's capabilities allow them;
  current models reject them (400).
Server-side refusal fallbacks are deliberately NOT enabled: in a benchmark they would silently swap the model
under test. A refusal is recorded as such (ResultStatus.REFUSED).
Thinking tokens are billed inside `output_tokens`; they are reported via `usage.output_tokens_details.thinking_tokens`.
"""

from __future__ import annotations

from typing import Any

import anthropic

from aidriven.adapters.llm.base import BaseAdapter, Stopwatch
from aidriven.domain.models import ProviderConnection
from aidriven.ports import DiscoveredModel, LLMRequest, LLMResponse, Message, ToolCall


class AnthropicAdapter(BaseAdapter):
    def __init__(self, connection: ProviderConnection, api_key: str | None) -> None:
        super().__init__(connection, api_key)
        kwargs: dict[str, Any] = {
            "timeout": connection.timeout_s,
            "max_retries": 0,
        }  # retries handled by BaseAdapter
        if api_key:
            kwargs["api_key"] = api_key
        if connection.base_url:
            kwargs["base_url"] = connection.base_url
        if connection.extra_headers:
            kwargs["default_headers"] = connection.extra_headers
        self.client = anthropic.AsyncAnthropic(**kwargs)

    def _is_transient(self, exc: Exception) -> bool:
        if isinstance(exc, (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APITimeoutError)):
            return True
        if isinstance(exc, anthropic.APIStatusError):
            return exc.status_code >= 500 or exc.status_code in (408, 409, 429)
        return False

    @staticmethod
    def _content(msg: Message) -> Any:
        if msg.role == "assistant" and msg.provider_raw is not None:
            return msg.provider_raw  # full content incl. thinking blocks, replayed unchanged
        if msg.role == "tool":
            return [{"type": "tool_result", "tool_use_id": msg.tool_call_id, "content": msg.text}]
        blocks: list[dict[str, Any]] = [
            {"type": "image", "source": {"type": "base64", "media_type": img.mime, "data": img.data_b64}}
            for img in msg.images
        ]
        if msg.text:
            blocks.append({"type": "text", "text": msg.text})
        for call in msg.tool_calls:
            blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments})
        return blocks

    def build_kwargs(self, request: LLMRequest, capabilities: Any = None) -> dict[str, Any]:
        p = request.params
        messages: list[dict[str, Any]] = []
        for m in request.messages:
            role = "user" if m.role == "tool" else m.role
            content = self._content(m)
            # Consecutive tool results must travel in a single user message.
            if messages and role == "user" and m.role == "tool" and messages[-1]["role"] == "user":
                messages[-1]["content"] = list(messages[-1]["content"]) + list(content)
            else:
                messages.append({"role": role, "content": content})
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": p.max_output_tokens,
            "messages": messages,
        }
        if request.system:
            kwargs["system"] = request.system
        if p.temperature is not None:
            kwargs["temperature"] = p.temperature
        if p.top_p is not None:
            kwargs["top_p"] = p.top_p
        if p.top_k is not None:
            kwargs["top_k"] = p.top_k
        if p.stop_sequences:
            kwargs["stop_sequences"] = p.stop_sequences
        if p.reasoning_budget is not None:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": p.reasoning_budget}
        elif p.reasoning_enabled is False:
            kwargs["thinking"] = {"type": "disabled"}
        elif p.reasoning_effort is not None or p.reasoning_enabled:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        if p.reasoning_effort is not None:
            kwargs["output_config"] = {"effort": p.reasoning_effort}
        if request.tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in request.tools
            ]
        if p.extra:
            kwargs["extra_body"] = dict(p.extra)
        return kwargs

    async def _generate(self, request: LLMRequest) -> LLMResponse:
        kwargs = self.build_kwargs(request)
        sw = Stopwatch()
        if request.params.stream:
            async with self.client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        sw.mark_first()
                message = await stream.get_final_message()
        else:
            message = await self.client.messages.create(**kwargs)
        return self._to_response(message, sw)

    @staticmethod
    def _to_response(message: Any, sw: Stopwatch) -> LLMResponse:
        text_parts, thinking_parts, calls = [], [], []
        for block in message.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "thinking":
                thinking_parts.append(block.thinking or "")
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {})))
        usage = message.usage
        cached = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        created = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        # Thinking tokens are billed inside output_tokens; newer API versions also break them out.
        details = getattr(usage, "output_tokens_details", None)
        if details is None and hasattr(usage, "model_extra"):
            details = (usage.model_extra or {}).get("output_tokens_details")
        thinking = (
            details.get("thinking_tokens") if isinstance(details, dict) else getattr(details, "thinking_tokens", 0)
        )
        return LLMResponse(
            text="".join(text_parts),
            reasoning_text="\n".join(t for t in thinking_parts if t),
            tool_calls=calls,
            # input_tokens from the API excludes cached/created prefix tokens: report the full prompt size.
            input_tokens=int(usage.input_tokens or 0) + cached + created,
            output_tokens=int(usage.output_tokens or 0),
            reasoning_tokens=int(thinking or 0),
            cached_tokens=cached,
            latency_ms=sw.elapsed_ms(),
            ttft_ms=sw.ttft_ms,
            finish_reason=message.stop_reason,
            refusal=message.stop_reason == "refusal",
            model_reported=message.model,
            raw_usage=usage.model_dump() if hasattr(usage, "model_dump") else {},
            provider_raw=[b.model_dump(exclude_none=True) for b in message.content],
        )

    async def list_models(self) -> list[DiscoveredModel]:
        out: list[DiscoveredModel] = []
        async for m in self.client.models.list():
            caps = getattr(m, "capabilities", None)
            vision = None
            if caps is not None:
                image = getattr(caps, "image_input", None)
                vision = bool(getattr(image, "supported", image)) if image is not None else None
            out.append(
                DiscoveredModel(
                    id=m.id,
                    label=getattr(m, "display_name", m.id),
                    context_window=getattr(m, "max_input_tokens", None),
                    max_output_tokens=getattr(m, "max_tokens", None),
                    vision=vision,
                    created=int(m.created_at.timestamp()) if getattr(m, "created_at", None) else None,
                )
            )
        return out
