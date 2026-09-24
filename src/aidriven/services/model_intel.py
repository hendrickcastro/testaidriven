"""Live model intelligence from OpenRouter's public model list (no key needed, cached 1 h).

Used to (1) enrich a provider's own model list with prices, context, max output, vision and release date,
(2) refresh curated catalog prices, and (3) infer capabilities of models the curated catalog does not know yet
(e.g. which sampling parameters a brand-new model accepts). Best effort: offline → curated data only.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from aidriven.domain.models import Capabilities, Pricing, ProviderKind, ReasoningMode
from aidriven.ports import DiscoveredModel

log = logging.getLogger(__name__)
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
TTL_S = 3600
_DATE_SUFFIX = re.compile(r"-(\d{4}-\d{2}-\d{2}|\d{8})$")

# OpenRouter vendor prefix for each provider kind (OpenRouter itself uses its ids as-is).
VENDOR = {
    ProviderKind.OPENAI: "openai/",
    ProviderKind.AZURE_OPENAI: "openai/",
    ProviderKind.ANTHROPIC: "anthropic/",
    ProviderKind.GEMINI: "google/",
}

# Ids of models that are not text chat/completion models (image, audio, embeddings, legacy completions...).
NON_CHAT = re.compile(
    r"(image|dall-e|tts|transcribe|whisper|realtime|audio|embedding|moderation|search-preview|search-api|"
    r"babbage|davinci|gpt-live|computer-use|veo|imagen|aqa|-live|native-audio|lyria)",
    re.IGNORECASE,
)


@dataclass
class ModelIntel:
    id: str
    name: str
    input_per_mtok: float | None
    output_per_mtok: float | None
    context_window: int | None
    max_output_tokens: int | None
    vision: bool
    params: set[str] = field(default_factory=set)
    created: int | None = None


_cache: dict[str, Any] = {"at": 0.0, "index": {}}


def _parse(item: dict[str, Any]) -> ModelIntel:
    pricing = item.get("pricing") or {}
    arch = item.get("architecture") or {}
    top = item.get("top_provider") or {}

    def per_mtok(v: Any) -> float | None:
        try:
            return round(float(v) * 1e6, 4)
        except (TypeError, ValueError):
            return None

    return ModelIntel(
        id=str(item["id"]),
        name=str(item.get("name") or item["id"]),
        input_per_mtok=per_mtok(pricing.get("prompt")),
        output_per_mtok=per_mtok(pricing.get("completion")),
        context_window=item.get("context_length"),
        max_output_tokens=top.get("max_completion_tokens"),
        vision="image" in (arch.get("input_modalities") or []),
        params=set(item.get("supported_parameters") or []),
        created=item.get("created"),
    )


async def load_index(force: bool = False) -> dict[str, ModelIntel]:
    if not force and time.time() - float(_cache["at"]) < TTL_S and _cache["index"]:
        return _cache["index"]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(OPENROUTER_MODELS_URL)
            r.raise_for_status()
            index = {m.id: m for m in (_parse(x) for x in r.json().get("data", [])) if not m.id.endswith(":batch")}
        _cache.update(at=time.time(), index=index)
        log.info("model intel: %d models from OpenRouter", len(index))
    except Exception as exc:
        log.warning("model intel unavailable (OpenRouter list): %s", exc)
    return _cache["index"]


def cached_index() -> dict[str, ModelIntel]:
    return _cache["index"]


def lookup(kind: ProviderKind, model_id: str, index: dict[str, ModelIntel] | None = None) -> ModelIntel | None:
    index = cached_index() if index is None else index
    if not index:
        return None
    prefix = "" if kind == ProviderKind.OPENROUTER else VENDOR.get(kind)
    if prefix is None:
        return None
    base = model_id.removeprefix("models/")
    for candidate in (base, _DATE_SUFFIX.sub("", base), base.replace(".", "-"), base.replace("-", ".", 1)):
        hit = index.get(prefix + candidate)
        if hit:
            return hit
    return None


def is_chat_model(model_id: str) -> bool:
    return not NON_CHAT.search(model_id)


def enrich(kind: ProviderKind, models: list[DiscoveredModel]) -> list[DiscoveredModel]:
    """Fill missing metadata from the intel index; newest first when release dates are known."""
    for m in models:
        intel = lookup(kind, m.id)
        if intel is None:
            continue
        m.label = m.label if m.label and m.label != m.id else intel.name.split(": ", 1)[-1]  # drop "Vendor: "
        m.context_window = m.context_window or intel.context_window
        m.max_output_tokens = m.max_output_tokens or intel.max_output_tokens
        m.vision = intel.vision if m.vision is None else m.vision
        if m.input_per_mtok is None:
            m.input_per_mtok = intel.input_per_mtok
        if m.output_per_mtok is None:
            m.output_per_mtok = intel.output_per_mtok
        m.created = m.created or intel.created
    models.sort(key=lambda m: (-(m.created or 0), m.id))
    return models


def capabilities_from_intel(kind: ProviderKind, model_id: str, intel: ModelIntel) -> Capabilities:
    """Best-effort capabilities for a model the curated catalog does not know."""
    p = intel.params
    reasoning = ReasoningMode.NONE
    efforts = ["low", "medium", "high"]
    if "reasoning" in p or "include_reasoning" in p:
        if kind == ProviderKind.GEMINI or model_id.startswith(("gemini", "google/gemini")):
            reasoning = ReasoningMode.LEVEL if re.search(r"gemini-[3-9]", model_id) else ReasoningMode.BUDGET
            efforts = ["low", "high"]
        else:
            reasoning = ReasoningMode.EFFORT
            if kind == ProviderKind.ANTHROPIC:
                efforts = ["low", "medium", "high", "xhigh", "max"]
    return Capabilities(
        vision=intel.vision,
        reasoning=reasoning,
        reasoning_efforts=efforts,
        temperature="temperature" in p,
        top_p="top_p" in p,
        top_k="top_k" in p,
        seed="seed" in p,
        stop_sequences="stop" in p,
        tools="tools" in p,
        json_mode="response_format" in p or "structured_outputs" in p,
        verbosity="verbosity" in p and kind in (ProviderKind.OPENAI, ProviderKind.AZURE_OPENAI),
        context_window=intel.context_window or 128_000,
        max_output_tokens=intel.max_output_tokens or 16_000,
    )


def pricing_from_intel(intel: ModelIntel) -> Pricing:
    return Pricing(input_per_mtok=intel.input_per_mtok or 0.0, output_per_mtok=intel.output_per_mtok or 0.0)
