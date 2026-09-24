# LLM port

Contract that isolates every provider. Everything the app asks a model — evaluated models, the judge, agents and connection tests — goes through `LLMPort` (`BaseAdapter` subclasses in `src/aidriven/adapters/llm/`). Compared with ContextAdmin's LLM layer (text in, text out), this port returns **full measurement data**: usage broken down, latency, TTFT, finish reason, refusal and raw usage.

## Types (`ports.py`, dataclasses)

```python
@dataclass
class ImagePart:   mime: str; data_b64: str

@dataclass
class ToolCall:    id: str; name: str; arguments: dict[str, Any]

@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    text: str = ""
    images: list[ImagePart] = []
    tool_calls: list[ToolCall] = []        # assistant turns that call tools
    tool_call_id: str | None = None        # tool turns: which call this answers
    tool_name: str | None = None
    provider_raw: Any = None               # provider-native assistant content, replayed verbatim in agent loops

@dataclass
class ToolSpec:    name: str; description: str; parameters: dict[str, Any]   # JSON Schema

@dataclass
class LLMRequest:
    model: str
    messages: list[Message]
    params: GenerationParams               # deep copy of the profile's params (judge: its own)
    system: str = ""
    tools: list[ToolSpec] = []
    metadata: dict[str, Any] = {}          # task_slug, run_id, repetition, role — ignored by real adapters

@dataclass
class LLMResponse:
    text: str = ""; reasoning_text: str = ""; tool_calls: list[ToolCall] = []
    input_tokens: int = 0; output_tokens: int = 0; reasoning_tokens: int = 0; cached_tokens: int = 0
    latency_ms: float = 0.0; ttft_ms: float | None = None
    finish_reason: str | None = None       # provider value as reported (not normalized)
    refusal: bool = False
    model_reported: str | None = None
    raw_usage: dict[str, Any] = {}
    provider_raw: Any = None               # assistant content to replay in tool loops
    retries: int = 0

class LLMError(Exception):  transient: bool; status: int | None
```

`output_tokens` includes reasoning tokens where the provider bills them together; `reasoning_tokens` is the reported breakdown (OpenAI, Gemini, Chat Completions `completion_tokens_details`). Missing values stay `0`/`None` ([P8](../00-overview/constitution.md)).

## Parameter mapping per adapter

Parameters that are `None`/empty are not sent. `validate_params()` (checked by the runner) guarantees only supported params are set.

| Concept (`GenerationParams`) | `AnthropicAdapter` | `OpenAIResponsesAdapter` (`openai`, `azure_openai`) | `GeminiAdapter` | `ChatCompletionsAdapter` — `openrouter` | `ChatCompletionsAdapter` — `openai_compatible` | `OllamaAdapter` |
|---|---|---|---|---|---|---|
| `reasoning_effort` | `thinking={"type":"adaptive","display":"summarized"}` + `output_config={"effort": e}` | `reasoning={"effort": e, "summary": "auto"}` + `include=["reasoning.encrypted_content"]` | `thinking_config.thinking_level = e` (+ `include_thoughts`) | `extra_body.reasoning.effort` | `reasoning_effort` | `think = e` |
| `reasoning_budget` | `thinking={"type":"enabled","budget_tokens": n}` (older models) | — | `thinking_config.thinking_budget = n` | `extra_body.reasoning.max_tokens` | — | — |
| `reasoning_enabled` | `False` → `thinking={"type":"disabled"}`; `True` → adaptive | — | `False` → `thinking_budget = 0`; `True` → `include_thoughts` | `extra_body.reasoning.enabled` | — | `think = true/false` |
| Reasoning text | `thinking` blocks | reasoning `summary` items | parts with `thought=True` | `message.reasoning` / `reasoning_content` | same | `message.thinking` |
| `verbosity` | — | `text.verbosity` | — | `verbosity` | `verbosity` | — |
| `temperature` / `top_p` / `top_k` | sent only if set (capabilities of current models forbid them) | `temperature`, `top_p` | `temperature`, `top_p`, `top_k` | `temperature`, `top_p`; `top_k` in `extra_body` | same | `options.temperature/top_p/top_k` |
| `seed` | — | — | `seed` | `seed` | `seed` | `options.seed` |
| `max_output_tokens` | `max_tokens` | `max_output_tokens` | `max_output_tokens` | `max_tokens` | `max_tokens` | `options.num_predict` |
| `stop_sequences` | `stop_sequences` | — | `stop_sequences` | `stop` | `stop` | `options.stop` |
| `json_mode` | — (not mapped) | `text.format={"type":"json_object"}` | `response_mime_type="application/json"` | `response_format={"type":"json_object"}` | same | `format="json"` |
| System prompt | `system` | `instructions` | `system_instruction` | first `system` message | same | first `system` message |
| Images | `image` block, base64 source | `input_image` with data URL | `Part.from_bytes` | `image_url` data URL | same | `images=[b64]` |
| Tools | `tools` (`input_schema`) + `tool_use`/`tool_result` (consecutive results grouped in one user message) | `function` tools + `function_call` / `function_call_output` items | `function_declarations`, automatic function calling **disabled** | OpenAI `tools` / `tool_calls` | same | `tools` / `tool_calls` |
| Replay in tool loops | full assistant content blocks (incl. thinking signatures) | full output items (incl. encrypted reasoning; `store=False`) | model `Content` with all parts | text + `tool_calls` | same | text + `tool_calls` |
| `extra` | `extra_body` | `extra_body` | merged into `GenerateContentConfig` | merged into `extra_body` | same | merged into the body |
| Streaming (`params.stream`) | `messages.stream`, TTFT at first `content_block_delta` | `stream=True`, TTFT at first `*.delta` event | stream only without tools | stream (with `include_usage`) only without tools | same | stream only without tools |
| Usage | `input_tokens + cache_read + cache_creation` as input; `cache_read_input_tokens` as cached; reasoning not reported | `usage.input/output_tokens`, `*_details.reasoning_tokens/cached_tokens` | `prompt_token_count`; output = `candidates + thoughts`; `thoughts_token_count`; `cached_content_token_count` | `prompt/completion_tokens` + details; OpenRouter `usage.include=true` | same | `prompt_eval_count`, `eval_count` |
| Refusal | `stop_reason == "refusal"` | `refusal` content part (text kept) | prompt `block_reason` or finish `SAFETY`/`PROHIBITED_CONTENT`/`BLOCKLIST`/`SPII` | `message.refusal` (non-streaming) | same | — (runner heuristic) |

- **Anthropic**: server-side refusal fallbacks are **deliberately not enabled** — in a benchmark they would silently swap the model under test; a refusal is recorded as `refused`. `max_retries=0` in the SDK (retries are ours).
- **Azure OpenAI** uses the same Responses adapter against the v1 endpoint: `base_url` (the resource endpoint) gets `/openai/v1/` appended unless it already contains `/openai/`; `model` = deployment name; `api_version` is not used.
- **OpenRouter** gets the default base URL `https://openrouter.ai/api/v1` and the header `X-Title: aidriven`; `openai_compatible` requires a base URL.
- **Ollama** uses the native `/api/chat` endpoint (for `think`, thinking text and exact token counts); `think` accepts a level string (gpt-oss) or a boolean.
- **Fake** (`FakeAdapter`, no network, 10 ms latency, tokens ≈ chars/4): `echo` (echoes the start of the prompt + `FINAL ANSWER: 42`), `oracle` (replays `<base_url or tests/fixtures/seed_solutions>/<task_slug>/response.txt`, or every file of that folder as ```` ```<lang> file=<name> ```` blocks; else `FINAL ANSWER: ?`), `judge` (valid verdict JSON, score 7), `flaky` (for ~30 % of prompts — chosen by a hash of prompt + repetition — raises a transient 503 on the **first** attempt only; the retry succeeds, so it exercises `Metrics.retries`), `tools` (first calls `run_python` with `print(6*7)`, then answers with the tool output).

## Cross-cutting rules (`BaseAdapter`)

1. **Validation before sending**: the runner calls `profile.validate_params()` before every item; unsupported params never reach an adapter.
2. **Timing**: a `Stopwatch` per call measures `latency_ms` (total) and `ttft_ms` (first delta when streaming; `None` otherwise, except the fake provider).
3. **Retries**: `with_retries` retries transient errors (adapter-specific: rate limit, connection, timeout, HTTP 408/409/429/5xx; Gemini `code` 408/429/5xx; Ollama transport errors and 429/5xx) with backoff `min(2·2^n + U(0,1), 30)` s, up to `ProviderConnection.max_retries`; the count is returned in `LLMResponse.retries`. Other exceptions are wrapped in `LLMError("<Type>: <message>", transient=…)`.
4. **Refusals**: explicit provider signals set `refusal=True`; the runner additionally treats short answers starting with typical refusal phrases and without files as refusals ([../03-modules/execution.md](../03-modules/execution.md)).
5. **Timeouts**: `ProviderConnection.timeout_s` per call (SDK/httpx timeout); the runner's `task_timeout_s` wraps the whole item.
6. **Connection test**: `test_connection(model)` = `list_models()` and, if a model is given, a non-streaming "Reply with the single word: pong" with `max_output_tokens=256`; returns `ConnectionTest(ok, latency_ms, detail)` (detail ≤ 500 chars on error).
7. **Mapping tests**: `tests/unit/test_adapters.py` asserts the request each adapter would send (Anthropic effort/budget/disabled and tool-loop replay, OpenAI Responses mapping, Azure base URL, OpenRouter extra body, Gemini thinking config, Ollama think/options/images) without network.

## Discovery

`list_models()` returns `DiscoveredModel {id, label, context_window?, max_output_tokens?, vision?, input_per_mtok?, output_per_mtok?}`; unknown fields stay `None` and are completed from the built-in catalog or by the user ([../03-modules/models-config.md](../03-modules/models-config.md)).
