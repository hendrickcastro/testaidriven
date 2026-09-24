"""LLM adapter factory: one adapter per ProviderConnection kind."""

from __future__ import annotations

from aidriven.adapters.llm.base import BaseAdapter
from aidriven.domain.models import ProviderConnection, ProviderKind


def build_adapter(connection: ProviderConnection, api_key: str | None) -> BaseAdapter:
    kind = connection.kind
    if kind == ProviderKind.ANTHROPIC:
        from aidriven.adapters.llm.anthropic_adapter import AnthropicAdapter

        return AnthropicAdapter(connection, api_key)
    if kind in (ProviderKind.OPENAI, ProviderKind.AZURE_OPENAI):
        from aidriven.adapters.llm.openai_adapter import OpenAIResponsesAdapter

        return OpenAIResponsesAdapter(connection, api_key)
    if kind in (ProviderKind.OPENROUTER, ProviderKind.OPENAI_COMPATIBLE):
        from aidriven.adapters.llm.openai_adapter import ChatCompletionsAdapter

        return ChatCompletionsAdapter(connection, api_key)
    if kind == ProviderKind.GEMINI:
        from aidriven.adapters.llm.gemini_adapter import GeminiAdapter

        return GeminiAdapter(connection, api_key)
    if kind == ProviderKind.OLLAMA:
        from aidriven.adapters.llm.ollama_adapter import OllamaAdapter

        return OllamaAdapter(connection, api_key)
    if kind == ProviderKind.FAKE:
        from aidriven.adapters.llm.fake_adapter import FakeAdapter

        return FakeAdapter(connection, api_key)
    raise ValueError(f"unsupported provider kind: {kind}")


__all__ = ["BaseAdapter", "build_adapter"]
