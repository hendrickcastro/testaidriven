# ADR-002 — Ports and adapters for LLM, persistence, artifacts, sandbox and secrets

- **Status**: Superseded by [ADR-011](ADR-011-ports-and-adapters-as-built.md) (2026-09-24)
- **Date**: 2026-09-24

## Context

The project's purpose is to compare **many** LLM providers, each with its own SDK, parameters and usage fields, and new ones appear constantly. Persistence must work offline (SQLite) and in the cloud (Firestore); artifacts local or in Firebase Storage; code execution in Docker or in a subprocess; secrets in the OS keyring or env vars. All of these are replaceable by requirement, and the domain (tasks, scoring, comparison) must be testable without network.

## Decision

Strict hexagonal architecture with five ports in `src/aidriven/ports/` (full signatures in [../../04-api/services.md](../../04-api/services.md)):

```python
class LLMPort(Protocol):            # one adapter per ProviderKind
    async def generate(self, profile: ModelProfile, request: LLMRequest,
                       on_token: Callable[[str], None] | None = None) -> LLMResponse: ...
    async def list_models(self) -> list[DiscoveredModel]: ...
    async def test_connection(self, model: str | None = None) -> ConnectionTest: ...

class RepositoryPort(Protocol):     # sqlite | firestore | memory
    async def get(self, cls: type[E], id: str) -> E | None: ...
    async def put(self, entity: E) -> E: ...
    async def list(self, cls: type[E], where: tuple[str, Any] | None = None) -> list[E]: ...

class ArtifactStorePort(Protocol):  # local | firebase
    async def save(self, run_id: str, result_id: str, name: str, data: bytes, mime: str) -> str: ...

class SandboxPort(Protocol):        # docker | process
    async def execute(self, job: SandboxJob) -> ExecutionRecord: ...

class SecretStorePort(Protocol):    # keyring (+ env fallback)
    def get(self, key: str) -> str | None: ...
```

Enforcement rules:

1. `domain/` imports no infrastructure package (`anthropic`, `openai`, `google.genai`, `firebase_admin`, `docker`, `keyring`, `nicegui`, `httpx`). Checked with ruff `flake8-tidy-imports` banned-api per folder.
2. Adapters are instantiated only in `composition.py`; services receive ports by constructor injection.
3. Prompts belong to the application (`prompts/`); **how** a call is made (streaming, JSON mode, tool calling, reasoning parameters) belongs to the adapter.
4. Every port has a shared **contract test suite** that runs against every adapter (fake/memory/sqlite always; docker/firestore/real providers under pytest markers).
5. The `fake` provider is a first-class adapter: deterministic responses for tests and demos without cost.

## Alternatives considered

- **LiteLLM / LangChain as the single LLM layer**: fewer adapters to write, but they normalize away exactly what we need to measure (reasoning tokens, cached tokens, TTFT, provider-specific effort/verbosity) and add another fast-moving dependency. An adapter may use one internally if convenient, never the domain.
- **Direct SDK calls from services**: less ceremony, but impossible to test offline and every new provider would touch the runner.

## Consequences

- (+) New provider/backend = one adapter + registration in `composition.py`.
- (+) Runner, judge, scoring and comparison are unit-tested with `FakeLLM` and the memory repository.
- (−) More boilerplate (request/response mapping per provider); accepted, it is the price of fair measurement.
