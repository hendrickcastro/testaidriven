# ADR-011 — Ports and adapters (as built): one `ports.py`, synchronous repository, `AppContext` composition root

- **Status**: Accepted — `RepositoryPort` (`delete_setting`, `list_settings`) and the secret-store adapters extended by [ADR-022](ADR-022-encrypted-api-keys-in-firestore.md)
- **Date**: 2026-09-24
- **Supersedes**: [ADR-002](ADR-002-ports-and-adapters.md)

## Context

ADR-002 specified five async `Protocol` ports in a `ports/` package, a `composition.py`, a memory repository and a shared contract-test suite per port. The implementation kept the hexagonal intent (the domain imports no SDK, every external technology sits behind an adapter) but simplified the ports: SQLite and the Firestore Admin SDK are synchronous, the repository is called from both async services and UI handlers, and the tests use real SQLite files in a temporary directory instead of a memory repository.

## Decision

1. All ports live in **`src/aidriven/ports.py`** (dataclasses + `typing.Protocol`):

```python
class LLMPort(Protocol):                 # one adapter instance per ProviderConnection
    connection: ProviderConnection
    async def generate(self, request: LLMRequest) -> LLMResponse: ...
    async def test_connection(self, model: str | None = None) -> ConnectionTest: ...
    async def list_models(self) -> list[DiscoveredModel]: ...

class RepositoryPort(Protocol):          # sqlite | firestore — SYNCHRONOUS
    backend: str
    def get(self, cls: type[E], entity_id: str) -> E | None: ...
    def find(self, cls: type[E], *, where: dict[str, Any] | None = None, limit: int | None = None) -> list[E]: ...
    def save(self, entity: E) -> E: ...                    # upsert, sets updated_at
    def save_many(self, entities: list[E]) -> None: ...
    def delete(self, cls: type[E], entity_id: str) -> None: ...
    def delete_where(self, cls: type[E], field_name: str, value: Any) -> int: ...
    def get_setting(self, key: str) -> dict[str, Any] | None: ...
    def set_setting(self, key: str, value: dict[str, Any]) -> None: ...

class ArtifactStorePort(Protocol):       # local | firebase — SYNCHRONOUS
    kind: Literal["local", "firebase"]
    def put(self, run_id: str, result_id: str, name: str, content: bytes, mime: str) -> str: ...  # location
    def get(self, location: str) -> bytes: ...
    def delete(self, location: str) -> None: ...

class SandboxPort(Protocol):             # docker | process
    isolation: Literal["docker", "process"]
    async def run(self, job: SandboxJob) -> ExecutionRecord: ...
    def available(self) -> bool: ...

class SecretStorePort(Protocol):         # keyring | memory
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...
```

2. **`AppContext` (`context.py`) is the composition root**: it loads `EnvSettings` and `AppConfig`, builds the secret store, the SQLite repository (and the Firestore one when enabled), the artifact stores, the `SandboxManager`, the `CheckEngine` and the `RunnerService`, caches one LLM adapter per connection (rebuilt when the connection or its key changes) and exposes the operational actions of *Settings* (install credentials, test/activate/deactivate Firestore, migrate, backup, test storage).
3. LLM adapters are built by `adapters.llm.build_adapter(connection, api_key)`; SDKs are imported lazily inside the adapter modules.
4. **Dependency rules actually enforced by review** (no import-linter): `domain/` imports only pydantic; `ports.py` imports only `domain`; adapters import `ports`, `domain` and `config`; services import `ports`, `domain` and a few adapter helpers that carry no SDK state (`BaseAdapter` as a type, `looks_like_refusal`, `catalog.find_entry`, `artifact_stores.safe_name`); `ui/` uses `AppContext`, services and the catalog/mask helpers.
5. Repository calls are synchronous; long SDK calls in *Settings* are pushed to `asyncio.to_thread` by the page. LLM and sandbox calls are async.
6. The `fake` provider is a first-class adapter (models `echo`, `oracle`, `judge`, `flaky`, `tools`) used by tests and by the offline demo created at bootstrap.
7. Tests use `SqliteRepository` on a temporary file, `MemorySecretStore` and the `ProcessSandbox` (see [../../08-roadmap/phases.md](../../08-roadmap/phases.md) for the test inventory). There is no separate per-port contract suite yet.

## Alternatives considered

- **Async repository with `asyncio.to_thread` in every adapter** (ADR-002): consistent signatures, but every UI handler and service would need `await` for millisecond SQLite reads.
- **Separate `ports/` package and `composition.py`**: more files for no behavioural gain at the current size.

## Consequences

- (+) New provider = one adapter class + one branch in `build_adapter`; new storage backend = one class with the same methods.
- (+) The whole run pipeline is testable offline with the fake provider and the process sandbox.
- (−) A slow Firestore call blocks the event loop while it runs (acceptable for a single-user tool; mitigated by doing migrations and tests in threads).
- (−) Adapter contract coverage relies on mapping tests (`tests/unit/test_adapters.py`) rather than a shared suite.
