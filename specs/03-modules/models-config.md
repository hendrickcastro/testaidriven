# Module: Model configuration

The *Models* page (`/models`), the provider adapters' `list_models`/`test_connection` and the built-in catalog (`adapters/llm/catalog.py`). Manages `ProviderConnection` (how to reach a provider) and `ModelProfile` (a concrete model with all its parameters — **the unit that is compared**). The design follows ContextAdmin's Administration page and ModelPicker: one card per provider, write-only keys, env-var fallback, connection test with latency, curated recommended models plus the live model list with one-click *Add* — extended with capabilities, reasoning parameters and pricing.

## Provider kinds

| `kind` | Adapter | `base_url` | Key env fallback | Discovery (`list_models`) |
|---|---|---|---|---|
| `anthropic` | `AnthropicAdapter` | optional | `ANTHROPIC_API_KEY` | `models.list()` (display name, max input/output tokens, image input) |
| `openai` | `OpenAIResponsesAdapter` | optional | `OPENAI_API_KEY` | `models.list()` (ids) |
| `azure_openai` | `OpenAIResponsesAdapter` | **required**: resource endpoint; `/openai/v1/` is appended | `AZURE_OPENAI_API_KEY` | `models.list()` on the v1 endpoint; model = deployment name |
| `gemini` | `GeminiAdapter` | optional | `GEMINI_API_KEY` | `models.list()` with `generateContent` (input/output token limits) |
| `openrouter` | `ChatCompletionsAdapter` | default `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` | `/models` (name, context, max completion tokens, image input, prices per token × 1e6) |
| `ollama` | `OllamaAdapter` | default `http://localhost:11434` | `OLLAMA_API_KEY` (optional) | `GET /api/tags` |
| `openai_compatible` | `ChatCompletionsAdapter` | **required** (e.g. `http://localhost:8000/v1`) | `OPENAI_COMPATIBLE_API_KEY` (optional) | `/models` |
| `fake` | `FakeAdapter` | optional: oracle solutions folder | none | `echo`, `oracle`, `judge`, `flaky`, `tools` |

Provider metadata for the UI comes from `catalog.PROVIDERS` (`ProviderInfo`: `label`, `description`, `key_url`, `docs_url`, `needs_key`, `base_url` = `none` | `optional` | `required`, `base_url_hint`, `multiple`). Keyless kinds: `ollama`, `openai_compatible`, `fake`; `base_url` required for `azure_openai` and `openai_compatible`, none for `fake`; `multiple=True` (several connections make sense) for `azure_openai` and `openai_compatible`.

## Page layout (after ContextAdmin's Admin / ModelPicker)

1. **Profiles** table on top (name — ⚖ marks the judge —, provider, model, reasoning, max tokens, features, price) with actions *Edit*, *Clone* (unique name "Copy of …"), *Use as judge*, *Test* (connection test with this profile's model), *Delete*; *Import* / *Export* profiles as JSON (import requires the referenced connection).
2. **Providers**: one expandable card per provider kind in `PROVIDERS` order (and one per existing connection of that kind). The header shows the name, a **Connected / Not configured** badge (ready = connection exists and has a key, or the kind needs none), the key source (saved / env) and the number of profiles; the description; links *Get an API key* and *Docs*.
   - **Connection form**: API key (password field; the label shows the current state — masked saved key, "from environment" or none; empty keeps the current key) for kinds that need one (and Ollama); base URL only where relevant (`*` when required, with a hint); **Advanced**: name, timeout (preset list, custom allowed), retries, enabled, API version (Azure only), extra headers (JSON). Buttons **Save** (creates the connection — id `provider_<kind>` for the first one — or updates it; the key goes to `AppContext.secrets`), **Test** (saves if needed, then `test_connection()` = list models, with latency), **Remove key** (when a key is saved), **Delete** (blocked while profiles use it; also deletes the key). A caption states whether keys are stored **encrypted in Firestore** or only locally ([ADR-022](../01-architecture/adr/ADR-022-encrypted-api-keys-in-firestore.md)).
   - **Recommended models** (once connected): the curated catalog rows for that kind, with badges for price (in/out per MTok, "free"), context, vision, reasoning mode and tools, the catalog notes, a counter of profiles already added, and a one-click **Add**.
   - **All models from the provider**: *Load models* runs `list_models()` (cached per connection for 10 minutes; *Load* forces a refresh), a filter box, up to 50 hits with price/context/vision/"in catalog" badges and **Add**.
   - Below the cards, **Add another connection** buttons for kinds with `multiple=True` (Azure OpenAI, OpenAI-compatible), named "<Label> <n>".
3. **Add** builds a ready-to-run profile with `profile_from_pick(connection, model_id, discovered)`: capabilities and prices from the catalog entry (or defaults), overridden by API metadata when the pick comes from the provider list (context window, max output, vision, prices); params = `max_output_tokens = min(16000, capability max)` updated with the catalog default params (e.g. `reasoning_effort=high`, `max_output_tokens=32000`, capped at the capability max); name `"<Label> · <effort>"` when an effort is set, made unique with a numeric suffix. Sampling params are never pre-filled. The profile is saved immediately and passes `validate_params()`.

## Profile editor (dropdown-driven)

Tabs *General* · *Parameters* (opened first when editing) · *Capabilities* · *Pricing*:

- **General**: name; model id as a **select** of catalog + discovered models (free input allowed) with a *refresh* button (forces discovery); the provider is fixed (shown as a caption); description, tags, system prompt.
- **Parameters** — every control is a select with "provider default" (= not sent) and presets, custom values allowed: reasoning effort/level from the declared levels (`effort`/`level` modes); thinking budget presets 1,024…65,536 up to `reasoning_budget_max` (`budget` mode); thinking *default / on / off* (any mode but `none`); max output tokens presets 1,024…128,000 up to the capability max; temperature, top_p, top_k, seed presets (shown only when supported); verbosity; JSON mode and streaming switches; **Advanced**: stop sequences (`|`-separated) and extra provider parameters (JSON, sent as-is, not validated). `validate_params()` problems are shown live and **block saving**.
- **Capabilities**: detected automatically (catalog + discovery) and shown as badges (vision, tools, JSON mode, streaming, sampling params, verbosity, reasoning mode, context, max output); **Re-detect** recomputes capabilities and prices with `profile_from_pick` for the selected model; **Override manually** (expansion) exposes the switches, reasoning mode, levels, budget max, context window and max output.
- **Pricing**: input / output / cached input per MTok, filled automatically.
- Saving requires name, connection and model; names are unique; editing increments `version`; renaming a profile that already has results shows a warning.

## Built-in catalog (catalog date 2026-09; prices USD / MTok, verify before relying on them)

| Kind | Model id | Reasoning | Notes |
|---|---|---|---|
| anthropic | `claude-fable-5-1` | effort `low…max` (5 levels) | 10 / 50 / cached 0.25; thinking always on |
| anthropic | `claude-opus-5-5` | effort (5 levels) | 4 / 20 / 0.20 |
| anthropic | `claude-opus-5`, `claude-opus-4-8` | effort (5 levels) | 5 / 25 / 0.50 |
| anthropic | `claude-sonnet-5` | effort (5 levels) | 2 / 10 / 0.20 |
| anthropic | `claude-sonnet-4-6` | effort `low, medium, high, max`; sampling params allowed | 3 / 15 / 0.30 |
| anthropic | `claude-haiku-4-5` | budget ≤ 60,000 | 1 / 5 / 0.10; context 200k, output 64k |
| openai | `gpt-5`, `gpt-5-mini` | effort `minimal…high`; verbosity; JSON mode; no temperature/top_p/stop | 1.25 / 10 / 0.125 · 0.25 / 2 / 0.025 |
| gemini | `gemini-3-pro-preview` | level `low, high` | 2 / 12 |
| gemini | `gemini-2.5-pro`, `gemini-2.5-flash` | budget ≤ 32,768 / 24,576 | 1.25 / 10 · 0.30 / 2.50 |
| ollama | `gpt-oss:20b` (effort), `qwen3:14b` (toggle) | — | local, free |
| fake | `echo`, `oracle`, `judge`, `flaky`, `tools` | — | 1 / 2 (so costs are non-zero in demos) |

Current Anthropic entries default to vision, tools, 1M context, 128k output, no temperature/top_p/top_k and `reasoning_effort=high`, `max_output_tokens=32000`.

## Business rules

- A profile `name` is part of `Result.key` and of comparison series labels; renaming breaks pairing with older runs.
- Editing a profile increments `version`; executed runs keep their snapshot copy ([P5](../00-overview/constitution.md)).
- A disabled connection makes its profiles fail at execution (`error` "provider … is disabled"); profiles stay selectable.
- The catalog and discovery only fill new profiles (and *Re-detect* on demand); nothing overwrites user-edited capabilities or pricing silently.
- The judge profile ([evaluation.md](evaluation.md)) is a normal profile selected in *Settings > Judge* or with *Use as judge*.
- API keys are never shown; only their source and a masked tail. With Firestore active they are stored encrypted there and cached locally ([ADR-022](../01-architecture/adr/ADR-022-encrypted-api-keys-in-firestore.md)).

## Acceptance criteria

- [x] With a key only in an env var, `resolve_api_key` reports source `env`; a saved key takes precedence (`test_infra.py::test_secret_resolution_prefers_saved_then_env`).
- [x] `validate_params` flags temperature on a model without temperature support and an effort level outside the declared list (`test_infra.py::test_profile_param_validation`).
- [x] `Pricing.cost` charges cached tokens at the cached rate (`test_infra.py::test_pricing_cost`).
- [x] Each adapter maps the profile params to its provider request as documented in [../05-llm/llm-port.md](../05-llm/llm-port.md) (`test_adapters.py`).
- [ ] A connection of each real kind can be created, its key saved to the keyring, tested (latency shown) and its models listed.
- [ ] No API key appears in plaintext in `data/aidriven.db`, Firestore, logs or task/run exports.
- [x] *Add* from the catalog yields a ready-to-run profile ("Claude Opus 5 · high", effort high, no temperature, 1M context, catalog price, valid params) (`test_adapters.py::test_profile_from_catalog_pick_is_ready_to_run`).
- [x] *Add* from the provider's list uses the API metadata (context, max output, vision, prices) (`test_adapters.py::test_profile_from_discovered_model_uses_api_metadata`).
- [ ] Cloning a profile and changing only `reasoning_effort` yields two comparable profiles.
- [x] The `fake` provider works without network and returns deterministic responses (used by every runner test).
