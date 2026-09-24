"""Provider parameter mapping (no network): what each adapter would send for a given profile."""

from aidriven.adapters.llm.anthropic_adapter import AnthropicAdapter
from aidriven.adapters.llm.gemini_adapter import GeminiAdapter
from aidriven.adapters.llm.ollama_adapter import OllamaAdapter
from aidriven.adapters.llm.openai_adapter import ChatCompletionsAdapter, OpenAIResponsesAdapter, _azure_base_url
from aidriven.domain.models import GenerationParams, ProviderConnection, ProviderKind
from aidriven.ports import ImagePart, LLMRequest, Message, ToolCall, ToolSpec

IMG = ImagePart(mime="image/png", data_b64="iVBORw0KGgo=")


def _req(**params: object) -> LLMRequest:
    return LLMRequest(
        model="m",
        system="sys",
        messages=[Message(role="user", text="hi", images=[IMG])],
        params=GenerationParams(**params),  # type: ignore[arg-type]
    )


def test_anthropic_effort_uses_adaptive_thinking_and_output_config() -> None:
    a = AnthropicAdapter(ProviderConnection(kind=ProviderKind.ANTHROPIC, name="a"), "k")
    kw = a.build_kwargs(_req(reasoning_effort="xhigh", max_output_tokens=32000))
    assert kw["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert kw["output_config"] == {"effort": "xhigh"}
    assert "temperature" not in kw
    assert kw["messages"][0]["content"][0]["type"] == "image"
    assert kw["system"] == "sys"


def test_anthropic_budget_and_disabled() -> None:
    a = AnthropicAdapter(ProviderConnection(kind=ProviderKind.ANTHROPIC, name="a"), "k")
    assert a.build_kwargs(_req(reasoning_budget=4096))["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    assert a.build_kwargs(_req(reasoning_enabled=False))["thinking"] == {"type": "disabled"}


def test_anthropic_groups_tool_results_and_replays_raw_content() -> None:
    a = AnthropicAdapter(ProviderConnection(kind=ProviderKind.ANTHROPIC, name="a"), "k")
    raw = [
        {"type": "thinking", "thinking": "", "signature": "s"},
        {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
    ]
    req = LLMRequest(
        model="m",
        params=GenerationParams(),
        messages=[
            Message(role="user", text="go"),
            Message(role="assistant", provider_raw=raw, tool_calls=[ToolCall("t1", "x", {})]),
            Message(role="tool", text="out1", tool_call_id="t1"),
            Message(role="tool", text="out2", tool_call_id="t2"),
        ],
        tools=[ToolSpec("x", "d", {"type": "object"})],
    )
    msgs = a.build_kwargs(req)["messages"]
    assert msgs[1]["content"] is raw
    assert [b["tool_use_id"] for b in msgs[2]["content"]] == ["t1", "t2"]
    assert len(msgs) == 3


def test_openai_responses_mapping() -> None:
    o = OpenAIResponsesAdapter(ProviderConnection(kind=ProviderKind.OPENAI, name="o"), "k")
    kw = o.build_kwargs(_req(reasoning_effort="high", verbosity="low", json_mode=True))
    assert kw["reasoning"] == {"effort": "high", "summary": "auto"}
    assert kw["text"] == {"verbosity": "low", "format": {"type": "json_object"}}
    assert kw["instructions"] == "sys"
    assert kw["store"] is False
    assert kw["input"][0]["content"][0]["type"] == "input_image"


def test_azure_base_url() -> None:
    assert _azure_base_url("https://res.openai.azure.com") == "https://res.openai.azure.com/openai/v1/"
    assert _azure_base_url("https://res.openai.azure.com/openai/v1/") == "https://res.openai.azure.com/openai/v1/"


def test_openrouter_reasoning_goes_to_extra_body() -> None:
    c = ChatCompletionsAdapter(ProviderConnection(kind=ProviderKind.OPENROUTER, name="r"), "k")
    kw = c.build_kwargs(_req(reasoning_effort="high", temperature=0.3, top_k=40))
    assert kw["extra_body"]["reasoning"] == {"effort": "high"}
    assert kw["extra_body"]["top_k"] == 40
    assert kw["temperature"] == 0.3
    assert kw["messages"][0] == {"role": "system", "content": "sys"}


def test_gemini_thinking_config() -> None:
    g = GeminiAdapter(ProviderConnection(kind=ProviderKind.GEMINI, name="g"), "k")
    cfg = g.build_config(_req(reasoning_budget=2048, seed=7))
    assert cfg.thinking_config.thinking_budget == 2048
    assert cfg.thinking_config.include_thoughts is True
    assert cfg.seed == 7
    lvl = g.build_config(_req(reasoning_effort="low"))
    assert str(lvl.thinking_config.thinking_level).lower().endswith("low")


def test_ollama_think_and_options() -> None:
    o = OllamaAdapter(ProviderConnection(kind=ProviderKind.OLLAMA, name="l"), None)
    body = o.build_body(_req(reasoning_effort="medium", temperature=0.1, max_output_tokens=100), stream=False)
    assert body["think"] == "medium"
    assert body["options"] == {"num_predict": 100, "temperature": 0.1}
    assert body["messages"][1]["images"] == [IMG.data_b64]


def test_profile_from_catalog_pick_is_ready_to_run(ctx) -> None:  # type: ignore[no-untyped-def]
    from aidriven.ui.common import set_context
    from aidriven.ui.pages.models import profile_from_pick

    set_context(ctx)
    conn = ProviderConnection(kind=ProviderKind.ANTHROPIC, name="Anthropic")
    prof = profile_from_pick(conn, "claude-opus-5")
    assert prof.name == "Claude Opus 5 · high"
    assert prof.params.reasoning_effort == "high"
    assert prof.params.temperature is None  # rejected by the model: never pre-filled
    assert prof.capabilities.context_window == 1_000_000
    assert prof.pricing.input_per_mtok == 5.0
    assert prof.validate_params() == []


def test_profile_from_discovered_model_uses_api_metadata(ctx) -> None:  # type: ignore[no-untyped-def]
    from aidriven.ports import DiscoveredModel
    from aidriven.ui.common import set_context
    from aidriven.ui.pages.models import profile_from_pick

    set_context(ctx)
    conn = ProviderConnection(kind=ProviderKind.OPENROUTER, name="OpenRouter")
    dm = DiscoveredModel(
        id="vendor/x",
        label="Vendor X",
        context_window=64000,
        max_output_tokens=4000,
        vision=True,
        input_per_mtok=0.5,
        output_per_mtok=1.5,
    )
    prof = profile_from_pick(conn, "vendor/x", dm)
    assert (prof.capabilities.context_window, prof.capabilities.vision) == (64000, True)
    assert prof.params.max_output_tokens == 4000
    assert (prof.pricing.input_per_mtok, prof.pricing.output_per_mtok) == (0.5, 1.5)


def test_model_intel_enrich_sort_and_infer() -> None:
    from aidriven.domain.models import ReasoningMode
    from aidriven.ports import DiscoveredModel
    from aidriven.services import model_intel as mi

    index = {
        "openai/gpt-new": mi._parse(
            {
                "id": "openai/gpt-new",
                "name": "OpenAI: GPT New",
                "context_length": 1_000_000,
                "created": 200,
                "pricing": {"prompt": "0.000002", "completion": "0.00001"},
                "architecture": {"input_modalities": ["text", "image"]},
                "top_provider": {"max_completion_tokens": 64000},
                "supported_parameters": ["reasoning", "tools", "seed", "response_format"],
            }
        ),
    }
    mi._cache.update(at=__import__("time").time(), index=index)
    models = [DiscoveredModel(id="babbage-002", created=100), DiscoveredModel(id="gpt-new-2026-09-01", created=None)]
    out = mi.enrich(ProviderKind.OPENAI, models)
    assert out[0].id == "gpt-new-2026-09-01"  # newest first (date from intel), dated alias resolved
    assert (out[0].input_per_mtok, out[0].context_window, out[0].vision) == (2.0, 1_000_000, True)
    assert not mi.is_chat_model("babbage-002") and not mi.is_chat_model("gpt-image-2") and mi.is_chat_model("gpt-6-sol")
    cap = mi.capabilities_from_intel(ProviderKind.OPENAI, "gpt-new", index["openai/gpt-new"])
    assert cap.reasoning == ReasoningMode.EFFORT and not cap.temperature and cap.tools and cap.seed and cap.json_mode
    assert cap.max_output_tokens == 64000
    mi._cache.update(at=0.0, index={})


def test_anthropic_reports_thinking_tokens() -> None:
    from types import SimpleNamespace

    from aidriven.adapters.llm.anthropic_adapter import AnthropicAdapter
    from aidriven.adapters.llm.base import Stopwatch

    usage = SimpleNamespace(
        input_tokens=38,
        output_tokens=389,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        output_tokens_details=SimpleNamespace(thinking_tokens=90),
        model_dump=lambda: {},
    )
    block = SimpleNamespace(type="text", text="FINAL ANSWER: 46", model_dump=lambda exclude_none=True: {})
    msg = SimpleNamespace(content=[block], usage=usage, stop_reason="end_turn", model="claude-opus-5-5")
    resp = AnthropicAdapter._to_response(msg, Stopwatch())
    assert (resp.output_tokens, resp.reasoning_tokens) == (389, 90)
