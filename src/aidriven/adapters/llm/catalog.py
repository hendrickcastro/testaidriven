"""Curated default catalog: capabilities + list prices per model (USD / 1M tokens).

The catalog only pre-fills new ModelProfiles; every value stays editable in the UI and live discovery
(`list_models`) adds models the catalog doesn't know. Prices change: verify against the provider before
relying on cost numbers. Catalog date: 2026-09-24 (prices cross-checked with OpenRouter; refreshed live
when reachable, see services/model_intel.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aidriven.domain.models import Capabilities, Pricing, ProviderKind, ReasoningMode

EFFORT_5 = ["low", "medium", "high", "xhigh", "max"]


@dataclass
class CatalogEntry:
    model: str
    label: str
    capabilities: Capabilities
    pricing: Pricing
    notes: str = ""
    default_params: dict[str, object] = field(default_factory=dict)


def _anthropic_current(model: str, label: str, inp: float, out: float, cached: float, notes: str = "") -> CatalogEntry:
    return CatalogEntry(
        model,
        label,
        Capabilities(
            vision=True,
            reasoning=ReasoningMode.EFFORT,
            reasoning_efforts=EFFORT_5,
            temperature=False,
            top_p=False,
            top_k=False,
            tools=True,
            context_window=1_000_000,
            max_output_tokens=128_000,
        ),
        Pricing(input_per_mtok=inp, output_per_mtok=out, cached_input_per_mtok=cached),
        notes or "Adaptive thinking; depth via effort. Sampling params rejected.",
        {"reasoning_effort": "high", "max_output_tokens": 32000},
    )


def _openai(
    model: str, label: str, inp: float, out: float, *, context: int = 1_050_000, notes: str = ""
) -> CatalogEntry:
    return CatalogEntry(
        model,
        label,
        Capabilities(
            vision=True,
            reasoning=ReasoningMode.EFFORT,
            reasoning_efforts=["low", "medium", "high", "xhigh"],
            temperature=False,
            top_p=False,
            seed=True,
            stop_sequences=False,
            tools=True,
            json_mode=True,
            verbosity=True,
            context_window=context,
            max_output_tokens=128_000,
        ),
        Pricing(input_per_mtok=inp, output_per_mtok=out),
        notes or "Responses API. Reasoning effort; sampling parameters are not accepted.",
        {"reasoning_effort": "high", "max_output_tokens": 32000},
    )


def _gemini(model: str, label: str, inp: float, out: float) -> CatalogEntry:
    return CatalogEntry(
        model,
        label,
        Capabilities(
            vision=True,
            reasoning=ReasoningMode.LEVEL,
            reasoning_efforts=["low", "high"],
            top_k=True,
            seed=True,
            tools=True,
            json_mode=True,
            context_window=1_048_576,
            max_output_tokens=65_536,
        ),
        Pricing(input_per_mtok=inp, output_per_mtok=out),
        "thinking_level low/high.",
        {"reasoning_effort": "high", "max_output_tokens": 32000},
    )


CATALOG: dict[ProviderKind, list[CatalogEntry]] = {
    ProviderKind.ANTHROPIC: [
        _anthropic_current(
            "claude-fable-5-1", "Claude Fable 5.1", 10.0, 50.0, 0.25, "Most capable. Thinking always on."
        ),
        _anthropic_current("claude-opus-5-5", "Claude Opus 5.5", 4.0, 20.0, 0.20, "Effort default is medium."),
        _anthropic_current("claude-opus-5", "Claude Opus 5", 5.0, 25.0, 0.50),
        _anthropic_current("claude-opus-4-8", "Claude Opus 4.8", 5.0, 25.0, 0.50),
        _anthropic_current("claude-sonnet-5", "Claude Sonnet 5", 2.0, 10.0, 0.20),
        CatalogEntry(
            "claude-sonnet-4-6",
            "Claude Sonnet 4.6",
            Capabilities(
                vision=True,
                reasoning=ReasoningMode.EFFORT,
                reasoning_efforts=["low", "medium", "high", "max"],
                top_k=True,
                tools=True,
                context_window=1_000_000,
                max_output_tokens=128_000,
            ),
            Pricing(input_per_mtok=3.0, output_per_mtok=15.0, cached_input_per_mtok=0.30),
        ),
        CatalogEntry(
            "claude-haiku-4-5",
            "Claude Haiku 4.5",
            Capabilities(
                vision=True,
                reasoning=ReasoningMode.BUDGET,
                reasoning_budget_max=60_000,
                top_k=True,
                tools=True,
                context_window=200_000,
                max_output_tokens=64_000,
            ),
            Pricing(input_per_mtok=1.0, output_per_mtok=5.0, cached_input_per_mtok=0.10),
            "Thinking via budget_tokens (min 1024, < max_tokens).",
        ),
    ],
    ProviderKind.OPENAI: [
        _openai("gpt-6-astra", "GPT-6 Astra", 10.0, 50.0, notes="Flagship. Most capable, highest cost."),
        _openai("gpt-6-sol", "GPT-6 Sol", 2.0, 10.0),
        _openai("gpt-6-luna", "GPT-6 Luna", 0.1, 0.5, notes="Very cheap and fast."),
        _openai("gpt-5.6-sol", "GPT-5.6 Sol", 2.0, 10.0),
        _openai("gpt-5.6-terra", "GPT-5.6 Terra", 2.0, 12.0),
        _openai("gpt-5.6-luna", "GPT-5.6 Luna", 0.2, 1.2),
        _openai("gpt-5.5", "GPT-5.5", 5.0, 30.0),
        _openai("gpt-5.4-mini", "GPT-5.4 mini", 0.75, 4.5, context=400_000),
        _openai("gpt-5.4-nano", "GPT-5.4 nano", 0.2, 1.25, context=400_000),
    ],
    ProviderKind.GEMINI: [
        _gemini("gemini-3.8-flash", "Gemini 3.8 Flash", 0.75, 3.75),
        _gemini("gemini-3.1-pro-preview", "Gemini 3.1 Pro (preview)", 2.0, 12.0),
        _gemini("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite", 0.3, 2.5),
        CatalogEntry(
            "gemini-2.5-pro",
            "Gemini 2.5 Pro",
            Capabilities(
                vision=True,
                reasoning=ReasoningMode.BUDGET,
                reasoning_budget_max=32_768,
                top_k=True,
                seed=True,
                tools=True,
                json_mode=True,
                context_window=1_000_000,
                max_output_tokens=65_536,
            ),
            Pricing(input_per_mtok=1.25, output_per_mtok=10.0),
            "Thinking via budget.",
        ),
    ],
    ProviderKind.OLLAMA: [
        CatalogEntry(
            "gpt-oss:20b",
            "gpt-oss 20B (local)",
            Capabilities(
                reasoning=ReasoningMode.EFFORT,
                top_k=True,
                seed=True,
                tools=True,
                json_mode=True,
                context_window=128_000,
            ),
            Pricing(),
            "Local, free. think = low/medium/high.",
        ),
        CatalogEntry(
            "qwen3:14b",
            "Qwen3 14B (local)",
            Capabilities(reasoning=ReasoningMode.TOGGLE, top_k=True, seed=True, tools=True, context_window=40_000),
            Pricing(),
        ),
    ],
    ProviderKind.FAKE: [
        CatalogEntry(
            m,
            f"Fake {m}",
            Capabilities(vision=True, tools=True, json_mode=True, seed=True, top_k=True),
            Pricing(input_per_mtok=1.0, output_per_mtok=2.0),
            "Deterministic fake model (no API calls).",
        )
        for m in ("echo", "oracle", "judge", "flaky", "tools")
    ],
}


def catalog_for(kind: ProviderKind) -> list[CatalogEntry]:
    if kind == ProviderKind.AZURE_OPENAI:
        return CATALOG[ProviderKind.OPENAI]
    return CATALOG.get(kind, [])


def find_entry(kind: ProviderKind, model: str) -> CatalogEntry | None:
    return next((e for e in catalog_for(kind) if e.model == model), None)


@dataclass
class ProviderInfo:
    kind: ProviderKind
    label: str
    description: str
    key_url: str = ""
    docs_url: str = ""
    needs_key: bool = True
    base_url: str = "optional"  # none | optional | required
    base_url_hint: str = ""
    multiple: bool = False  # several connections of this kind make sense (e.g. two Azure resources)


PROVIDERS: list[ProviderInfo] = [
    ProviderInfo(
        ProviderKind.ANTHROPIC,
        "Anthropic",
        "Claude models (Messages API). Adaptive thinking with effort levels on current models.",
        "https://console.anthropic.com/settings/keys",
        "https://docs.anthropic.com/en/docs/about-claude/models",
    ),
    ProviderInfo(
        ProviderKind.OPENAI,
        "OpenAI",
        "GPT and o-series models through the Responses API (reasoning effort, verbosity).",
        "https://platform.openai.com/api-keys",
        "https://platform.openai.com/docs/models",
    ),
    ProviderInfo(
        ProviderKind.GEMINI,
        "Google Gemini",
        "Gemini models (thinking budget on 2.5, thinking level on 3).",
        "https://aistudio.google.com/apikey",
        "https://ai.google.dev/gemini-api/docs/models",
    ),
    ProviderInfo(
        ProviderKind.OPENROUTER,
        "OpenRouter",
        "Hundreds of models from many vendors behind one key, with live prices.",
        "https://openrouter.ai/keys",
        "https://openrouter.ai/models",
    ),
    ProviderInfo(
        ProviderKind.AZURE_OPENAI,
        "Azure OpenAI",
        "OpenAI models deployed in your Azure resource (model id = deployment name).",
        "https://portal.azure.com/",
        "https://learn.microsoft.com/azure/ai-services/openai/",
        base_url="required",
        base_url_hint="https://<resource>.openai.azure.com",
        multiple=True,
    ),
    ProviderInfo(
        ProviderKind.OLLAMA,
        "Ollama",
        "Local open-weight models (free). Start Ollama and pull a model first.",
        "",
        "https://ollama.com/library",
        needs_key=False,
        base_url="optional",
        base_url_hint="http://localhost:11434",
    ),
    ProviderInfo(
        ProviderKind.OPENAI_COMPATIBLE,
        "OpenAI-compatible",
        "Any server speaking the OpenAI Chat Completions API (vLLM, LM Studio, llama.cpp…).",
        "",
        "",
        needs_key=False,
        base_url="required",
        base_url_hint="http://localhost:8000/v1",
        multiple=True,
    ),
    ProviderInfo(
        ProviderKind.FAKE,
        "Fake (offline demo)",
        "Deterministic fake models for demos and tests: no API calls, zero cost.",
        needs_key=False,
        base_url="none",
    ),
]


def provider_info(kind: ProviderKind) -> ProviderInfo:
    return next(p for p in PROVIDERS if p.kind == kind)
