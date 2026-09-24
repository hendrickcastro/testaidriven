# Internal service contracts

AIDriven has no public HTTP API: NiceGUI pages call the `AppContext` and the services, services call **ports** ([ADR-011](../01-architecture/adr/ADR-011-ports-and-adapters-as-built.md)). This document is the normative contract of both as implemented. Entities come from [`domain/models.py`](../../src/aidriven/domain/models.py); LLM request/response types are specified in [../05-llm/llm-port.md](../05-llm/llm-port.md).

## Conventions

- Errors are plain exceptions with English messages: `ValueError` (validation, not found), `RuntimeError` (configuration/availability), `LLMError(message, transient: bool, status: int | None)` from adapters. The UI shows them with `notify_error` (`"<Type>: <message>"`).
- Repository and artifact-store calls are **synchronous**; LLM, sandbox, check, judge and agent calls are `async`.
- `E = TypeVar("E", bound=Entity)`. No method returns or logs secrets.

## Ports (`src/aidriven/ports.py`)

```python
class LLMPort(Protocol):
    connection: ProviderConnection
    async def generate(self, request: LLMRequest) -> LLMResponse: ...          # raises LLMError
    async def test_connection(self, model: str | None = None) -> ConnectionTest: ...  # ConnectionTest(ok, latency_ms, detail)
    async def list_models(self) -> list[DiscoveredModel]: ...

class RepositoryPort(Protocol):
    backend: str                                                               # "sqlite" | "firestore"
    def get(self, cls: type[E], entity_id: str) -> E | None: ...
    def find(self, cls: type[E], *, where: dict[str, Any] | None = None, limit: int | None = None) -> list[E]: ...
    def save(self, entity: E) -> E: ...
    def save_many(self, entities: list[E]) -> None: ...
    def delete(self, cls: type[E], entity_id: str) -> None: ...
    def delete_where(self, cls: type[E], field_name: str, value: Any) -> int: ...
    def get_setting(self, key: str) -> dict[str, Any] | None: ...
    def set_setting(self, key: str, value: dict[str, Any]) -> None: ...
    def delete_setting(self, key: str) -> None: ...
    def list_settings(self, prefix: str) -> dict[str, dict[str, Any]]: ...

class ArtifactStorePort(Protocol):
    kind: Literal["local", "firebase"]
    def put(self, run_id: str, result_id: str, name: str, content: bytes, mime: str) -> str: ...   # location
    def get(self, location: str) -> bytes: ...
    def delete(self, location: str) -> None: ...
    # FirebaseStorageArtifactStore also offers health_check() -> str, used by Settings > Storage
    # FirestoreRepository also offers health_check() -> str, used by Settings > Firestore

class SandboxPort(Protocol):
    isolation: Literal["docker", "process"]
    async def run(self, job: SandboxJob) -> ExecutionRecord: ...
    def available(self) -> bool: ...

class SecretStorePort(Protocol):          # KeyringSecretStore | MemorySecretStore | EncryptedRepoSecretStore(repo, fernet_key, cache)
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...

@dataclass
class SandboxJob:
    files: dict[str, bytes]; command: list[str]; timeout_s: float = 60.0
    needs_browser: bool = False; collect: list[str] = []
```

Helpers next to the adapters: `resolve_api_key(store, secret_key, kind) -> (key | None, "saved" | "env" | None)`, `mask(secret)`, `derive_fernet_key(material) -> bytes`, `encode_nested`/`decode_nested` (Firestore), `safe_name(name)`, `build_adapter(connection, api_key) -> BaseAdapter`, `catalog_for(kind)`, `find_entry(kind, model)`, `init_firestore_app(...)`.

## Composition root — `AppContext` (`context.py`)

```python
class AppContext:
    env: EnvSettings; config_store: ConfigStore; local_secrets: SecretStorePort   # keyring (or memory)
    secrets: SecretStorePort               # property: encrypted Firestore store while Firestore is active, else local_secrets
    sqlite: SqliteRepository | None; local_store: LocalArtifactStore
    sandbox: SandboxManager; check_engine: CheckEngine; runner: RunnerService
    config: AppConfig                      # property
    repo: RepositoryPort; backend: str     # properties (active repository)
    def save_config(self) -> None          # persists data/config.json, pushes sandbox config
    def install_credentials(self, content: bytes) -> dict[str, str]   # {project_id, client_email}
    def test_firestore(self) -> tuple[bool, str]
    def activate_firestore(self) -> None
    def deactivate_firestore(self) -> None
    def migrate_local_to_firestore(self) -> dict[str, int]            # count per collection + "api_keys" uploaded
    def backup_local(self) -> Path                                     # data/backups/aidriven-<ts>.db
    def artifact_store(self) -> ArtifactStorePort                      # for NEW artifacts (cloud toggle)
    def artifact_store_for(self, kind: str) -> ArtifactStorePort       # "local" | "firebase"
    def test_storage(self) -> tuple[bool, str]
    def api_key_for(self, connection) -> tuple[str | None, str | None]
    def adapter_for_connection(self, connection) -> BaseAdapter         # cached by connection+key fingerprint
    def adapter_for(self, profile) -> BaseAdapter                       # RuntimeError if missing/disabled
    def judge_profile(self) -> ModelProfile | None                      # judge.enabled and judge.model_id
```

## Services (`src/aidriven/services/`)

```python
class RunnerService:                                  # runner.py
    live: dict[str, LiveRun]
    def create_run(self, *, name, task_ids, model_ids, ruleset_ids=None, agent_id=None, repetitions=1,
                   concurrency=4, task_timeout_s=900.0, judge_enabled=True, suite_id=None) -> Run
    def create_from_suite(self, suite: Suite, name: str | None = None) -> Run
    def create_rerun(self, run_id, *, scope: Literal["full", "tasks", "results"] = "full",
                     task_slugs=None, model_names=None, result_ids=None,
                     use_current_versions=False, repetitions: int | None = None) -> Run
    def start(self, run_id) -> LiveRun               # also resumes: runs every non-terminal result
    def cancel(self, run_id) -> None
    def is_running(self, run_id) -> bool
    async def process(self, run, result, task, profile) -> Result
    async def reevaluate(self, result_id, rejudge: bool = True) -> Result
    def load_files(self, result) -> dict[str, str]
    def set_user_score(self, result_id, score: float | None, comment: str = "") -> Result
    def accept_judge(self, result_id) -> Result
    def delete_run(self, run_id) -> None

class AgentRunner:                                    # agent.py
    def __init__(self, adapter, sandbox, agent: AgentDef, task: Task)
    async def run(self, request: LLMRequest) -> AgentOutcome   # final_text, responses, transcript, files, tool_calls

class CheckEngine:                                    # checks.py
    def __init__(self, sandbox: SandboxPort)
    async def run_all(self, checks, response_text, files: dict[str, str]) -> list[CheckResult]
    async def run_one(self, check, response_text, files) -> CheckResult
# helpers: extract_final_answer(text, pattern=None), normalize(value, case_sensitive), parse_number(text), harness_source()

class JudgeService:                                   # judge.py
    def __init__(self, adapter: BaseAdapter, profile: ModelProfile)
    async def judge(self, task, answer: str, checks, evaluated_model: str) -> JudgeVerdict
# helpers: summarize_checks(checks), parse_verdict(text)

# scoring.py
def auto_score(checks) -> float | None
def aggregate(model_name, results) -> ModelAggregate
def compute_dimensions(aggs: list[ModelAggregate]) -> None      # fills .dims
def aggregate_by_model(results) -> list[ModelAggregate]

# comparison.py
def compare(runs, results_by_run: dict[str, list[Result]], model_filter=None) -> Comparison  # series, rows, baseline

# artifacts.py
def extract_files(text, expected_names=None) -> list[ExtractedFile]; def kind_for(name) -> ArtifactKind

# prompting.py
def build_request(task, profile, rulesets, agent=None, metadata=None) -> LLMRequest
def compose_system(profile, rulesets, agent) -> str; def compose_user_message(task, vision) -> Message
def missing_capabilities(task, profile) -> list[str]; def attachment_bytes(att) -> bytes

# task_io.py
def load_seed_tasks() -> list[Task]; def sync_seed_tasks(repo) -> int
def export_tasks(tasks, fmt="yaml" | "json") -> str; def parse_tasks(text) -> list[Task]
def import_tasks(repo, text) -> tuple[int, int]              # (created, updated)

# bootstrap.py
def bootstrap(repo) -> None                                  # recover interrupted runs + idempotent first-start data + seed sync
def recover_interrupted_runs(repo) -> int                    # running runs → partial, running results → pending
```

Logging: `logging_setup.setup_logging(logs_dir) -> LogCapture` (every handler has a `RedactingFilter`; `redact(text)` is also public); `CAPTURE.snapshot()`, `hints()`, `dismiss_hint(url)`, `clear()` ([../03-modules/logs.md](../03-modules/logs.md)).

## Result types

```python
@dataclass
class LiveRun:  run_id: str; total: int; done: int; active: dict[str, str]; events: deque[str]  # maxlen 300
                task: asyncio.Task | None; cancelled: bool

@dataclass
class ModelAggregate:   # scoring.py — counts, success_rate, mean_final/auto/judge/user, score_stdev, pass_at_k,
                        # pass_all_k, conformity_rate, intelligence_rate, latency p50/p95, ttft p50, tokens_per_s,
                        # token sums, cost_usd, dims: dict[str, float | None]

@dataclass
class Comparison:  series: list[Series]; rows: list[TaskRow]; baseline: str | None
class Series:      key, label, run_id, run_name, model_name, aggregate
class TaskRow:     task_slug; cells: dict[series_key, TaskCell]; deltas: dict[series_key, dict[metric, float | None]]
```

## Acceptance criteria

- [x] The run pipeline (`RunnerService`, `AgentRunner`, `CheckEngine`, `JudgeService`, scoring and comparison) is unit-tested offline with the fake provider, SQLite in a temp dir and the process sandbox (`tests/unit/test_runner.py`, `test_checks.py`).
- [ ] A contract test suite per port runs against all its adapters (markers `docker`/`firestore` for the external ones).
- [ ] Services depend only on ports and SDK-free adapter helpers (import-lint check).
