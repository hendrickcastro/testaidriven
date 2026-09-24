"""Ollama native API adapter (`/api/chat`, `/api/tags`) over httpx.

Native API instead of the OpenAI shim because it exposes `think`, `thinking` text and exact token counts
(`prompt_eval_count`, `eval_count`).
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from aidriven.adapters.llm.base import BaseAdapter, Stopwatch
from aidriven.domain.models import ProviderConnection
from aidriven.ports import DiscoveredModel, LLMError, LLMRequest, LLMResponse, ToolCall

DEFAULT_URL = "http://localhost:11434"


class OllamaAdapter(BaseAdapter):
    def __init__(self, connection: ProviderConnection, api_key: str | None) -> None:
        super().__init__(connection, api_key)
        headers = dict(connection.extra_headers)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.client = httpx.AsyncClient(
            base_url=(connection.base_url or DEFAULT_URL).rstrip("/"),
            timeout=connection.timeout_s,
            headers=headers,
        )

    def _is_transient(self, exc: Exception) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code >= 500 or exc.response.status_code == 429
        return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))

    def build_body(self, request: LLMRequest, stream: bool) -> dict[str, Any]:
        p = request.params
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for m in request.messages:
            msg: dict[str, Any] = {"role": m.role, "content": m.text}
            if m.images:
                msg["images"] = [i.data_b64 for i in m.images]
            if m.tool_calls:
                msg["tool_calls"] = [{"function": {"name": c.name, "arguments": c.arguments}} for c in m.tool_calls]
            if m.role == "tool" and m.tool_name:
                msg["tool_name"] = m.tool_name
            messages.append(msg)
        options: dict[str, Any] = {"num_predict": p.max_output_tokens}
        for name in ("temperature", "top_p", "top_k", "seed"):
            if getattr(p, name) is not None:
                options[name] = getattr(p, name)
        if p.stop_sequences:
            options["stop"] = p.stop_sequences
        body: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "stream": stream,
            "options": options,
        }
        if p.reasoning_effort is not None:
            body["think"] = p.reasoning_effort  # gpt-oss style levels
        elif p.reasoning_enabled is not None:
            body["think"] = p.reasoning_enabled
        if p.json_mode:
            body["format"] = "json"
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in request.tools
            ]
        body.update(p.extra)
        return body

    async def _generate(self, request: LLMRequest) -> LLMResponse:
        stream = request.params.stream and not request.tools
        body = self.build_body(request, stream)
        sw = Stopwatch()
        text, thinking, calls, final = [], [], [], {}
        if stream:
            async with self.client.stream("POST", "/api/chat", json=body) as r:
                if r.status_code >= 400:
                    await r.aread()
                    r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if "error" in chunk:
                        raise LLMError(f"Ollama: {chunk['error']}")
                    msg = chunk.get("message") or {}
                    if msg.get("content") or msg.get("thinking"):
                        sw.mark_first()
                    text.append(msg.get("content") or "")
                    thinking.append(msg.get("thinking") or "")
                    if chunk.get("done"):
                        final = chunk
        else:
            r = await self.client.post("/api/chat", json=body)
            r.raise_for_status()
            final = r.json()
            msg = final.get("message") or {}
            text.append(msg.get("content") or "")
            thinking.append(msg.get("thinking") or "")
            for i, tc in enumerate(msg.get("tool_calls") or []):
                fn = tc.get("function") or {}
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args or "{}")
                calls.append(
                    ToolCall(id=f"call_{int(time.time() * 1000)}_{i}", name=fn.get("name", ""), arguments=args)
                )
        return LLMResponse(
            text="".join(text),
            reasoning_text="".join(thinking),
            tool_calls=calls,
            input_tokens=int(final.get("prompt_eval_count") or 0),
            output_tokens=int(final.get("eval_count") or 0),
            latency_ms=sw.elapsed_ms(),
            ttft_ms=sw.ttft_ms,
            finish_reason=final.get("done_reason"),
            model_reported=final.get("model"),
            raw_usage={k: v for k, v in final.items() if k.endswith(("_count", "_duration"))},
        )

    async def list_models(self) -> list[DiscoveredModel]:
        r = await self.client.get("/api/tags")
        r.raise_for_status()
        return [DiscoveredModel(id=m["name"], label=m.get("name", "")) for m in r.json().get("models", [])]
