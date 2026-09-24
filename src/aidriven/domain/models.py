"""Domain model. Canonical terms: see specs/00-overview/glossary.md.

Every persisted entity inherits from `Entity` (id + timestamps) and is stored as a JSON document
in the `aidriven_<collection>` collection (constitution P1).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class Entity(BaseModel):
    """Base of every persisted document."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)
    collection: ClassVar[str] = ""

    id: str = Field(default_factory=new_id)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------------------------------
# Providers and model profiles
# --------------------------------------------------------------------------------------------------


class ProviderKind(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    GEMINI = "gemini"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"
    FAKE = "fake"  # deterministic, for tests and zero-cost demos


class ProviderConnection(Entity):
    """Connection to a provider. The API key is NEVER stored here: it lives in the SecretStore (P6)."""

    collection: ClassVar[str] = "providers"

    kind: ProviderKind
    name: str
    base_url: str | None = None
    api_version: str | None = None  # Azure OpenAI
    extra_headers: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = 600.0
    max_retries: int = 2
    enabled: bool = True

    @property
    def secret_key(self) -> str:
        return f"provider:{self.id}:api_key"


class ReasoningMode(StrEnum):
    NONE = "none"  # the model has no configurable reasoning
    EFFORT = "effort"  # discrete levels (OpenAI reasoning.effort, Anthropic effort)
    BUDGET = "budget"  # thinking token budget (Anthropic thinking, Gemini 2.5)
    LEVEL = "level"  # Gemini 3 thinking_level
    TOGGLE = "toggle"  # on/off (Ollama think)


class Capabilities(BaseModel):
    """What a model supports. Drives which parameters the UI shows and which ones the runner validates."""

    vision: bool = False
    reasoning: ReasoningMode = ReasoningMode.NONE
    reasoning_efforts: list[str] = Field(default_factory=lambda: ["low", "medium", "high"])
    reasoning_budget_max: int = 32000
    temperature: bool = True
    top_p: bool = True
    top_k: bool = False
    seed: bool = False
    stop_sequences: bool = True
    tools: bool = False
    json_mode: bool = False
    streaming: bool = True
    verbosity: bool = False  # OpenAI GPT-5 text.verbosity
    context_window: int = 128_000
    max_output_tokens: int = 8192


class Pricing(BaseModel):
    """USD per million tokens."""

    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    cached_input_per_mtok: float | None = None

    def cost(self, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
        cached_rate = self.cached_input_per_mtok if self.cached_input_per_mtok is not None else self.input_per_mtok
        fresh = max(input_tokens - cached_tokens, 0)
        return (fresh * self.input_per_mtok + cached_tokens * cached_rate + output_tokens * self.output_per_mtok) / 1e6


class GenerationParams(BaseModel):
    """Generation parameters. `None` = not sent (provider default applies)."""

    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    max_output_tokens: int = 8192
    seed: int | None = None
    stop_sequences: list[str] = Field(default_factory=list)
    reasoning_effort: str | None = None
    reasoning_budget: int | None = None
    reasoning_enabled: bool | None = None
    verbosity: Literal["low", "medium", "high"] | None = None
    json_mode: bool = False
    stream: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)  # raw passthrough to the provider


class ModelProfile(Entity):
    """A concrete model with its configuration. This is the unit being compared."""

    collection: ClassVar[str] = "models"

    name: str
    provider_id: str
    model: str
    description: str = ""
    capabilities: Capabilities = Field(default_factory=Capabilities)
    params: GenerationParams = Field(default_factory=GenerationParams)
    pricing: Pricing = Field(default_factory=Pricing)
    system_prompt: str = ""
    tags: list[str] = Field(default_factory=list)
    version: int = 1

    def validate_params(self) -> list[str]:
        """Return parameter problems against capabilities (empty = ok)."""
        p, c, problems = self.params, self.capabilities, []
        if p.temperature is not None and not c.temperature:
            problems.append("temperature is not supported by the model")
        if p.top_p is not None and not c.top_p:
            problems.append("top_p is not supported by the model")
        if p.top_k is not None and not c.top_k:
            problems.append("top_k is not supported by the model")
        if p.seed is not None and not c.seed:
            problems.append("seed is not supported by the model")
        if p.verbosity is not None and not c.verbosity:
            problems.append("verbosity is not supported by the model")
        if p.json_mode and not c.json_mode:
            problems.append("json_mode is not supported by the model")
        if p.max_output_tokens > c.max_output_tokens:
            problems.append(f"max_output_tokens {p.max_output_tokens} > model maximum {c.max_output_tokens}")
        if p.reasoning_effort is not None:
            if c.reasoning not in (ReasoningMode.EFFORT, ReasoningMode.LEVEL):
                problems.append("reasoning_effort requires a model with level-based reasoning")
            elif p.reasoning_effort not in c.reasoning_efforts:
                problems.append(f"reasoning_effort '{p.reasoning_effort}' is not one of {c.reasoning_efforts}")
        if p.reasoning_budget is not None:
            if c.reasoning != ReasoningMode.BUDGET:
                problems.append("reasoning_budget requires a model with a thinking budget")
            elif p.reasoning_budget > c.reasoning_budget_max:
                problems.append(f"reasoning_budget > maximum {c.reasoning_budget_max}")
        if p.reasoning_enabled is not None and c.reasoning == ReasoningMode.NONE:
            problems.append("the model has no configurable reasoning")
        return problems


# --------------------------------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------------------------------


class ArtifactKind(StrEnum):
    TEXT = "text"
    PYTHON = "python"
    HTML = "html"
    JAVASCRIPT = "javascript"
    JSON = "json"
    SQL = "sql"
    MARKDOWN = "markdown"
    BINARY = "binary"


class CheckKind(StrEnum):
    EXACT = "exact"  # final answer == expected (normalized)
    REGEX = "regex"  # the response (or an artifact) matches a pattern
    NUMERIC = "numeric"  # final number within tolerance
    JSON_SCHEMA = "json_schema"  # JSON response/artifact valid against a schema
    PYTHON_TESTS = "python_tests"  # hidden tests against the python artifact, in the sandbox
    PYTHON_VERIFIER = "python_verifier"  # custom script that receives response and artifacts, in the sandbox
    HTML_PLAYWRIGHT = "html_playwright"  # playwright script against the html artifact, in the sandbox
    SQL_RESULT = "sql_result"  # the SQL artifact run over a given database yields the expected rows


class Check(BaseModel):
    """Automatic verification. Its outcome feeds the `auto` score and the dimensions."""

    id: str = Field(default_factory=lambda: new_id("chk_"))
    kind: CheckKind
    name: str
    dimension: Literal["intelligence", "conformity"] = "intelligence"
    weight: float = 1.0
    # Parameters depending on kind
    expected: Any = None  # exact / numeric / sql_result
    pattern: str | None = None  # regex; or regex to extract the final answer (exact/numeric)
    tolerance: float = 1e-6  # numeric (absolute)
    case_sensitive: bool = False  # exact
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")  # json_schema
    target: str | None = None  # artifact name it acts on (None = response / first of the kind)
    code: str | None = None  # python_tests / python_verifier / html_playwright
    files: dict[str, str] = Field(default_factory=dict)  # auxiliary (text) files for the sandbox
    timeout_s: float = 60.0

    model_config = ConfigDict(populate_by_name=True)


class Attachment(BaseModel):
    """Task attachment: images for vision or context documents."""

    name: str
    mime: str
    # Content: path relative to seed/assets, or inline base64 for user-uploaded attachments.
    path: str | None = None
    data_b64: str | None = None
    inline_text: bool = False  # if text, it is embedded into the prompt


class Task(Entity):
    """A battery task. Versioned: editing bumps `version`, and runs keep a snapshot."""

    collection: ClassVar[str] = "tasks"

    slug: str
    title: str
    category: str
    difficulty: Literal["medium", "hard", "extreme"] = "hard"
    prompt: str
    attachments: list[Attachment] = Field(default_factory=list)
    requires: list[Literal["vision", "tools", "json_mode"]] = Field(default_factory=list)
    expected_artifact: ArtifactKind = ArtifactKind.TEXT
    artifact_names: list[str] = Field(default_factory=list)  # requested files (e.g. ["solver.py"])
    rubric: str = ""  # criteria for the judge
    reference_answer: str = ""  # reference answer for the judge (never shown to the evaluated model)
    checks: list[Check] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source: Literal["seed", "user", "import"] = "user"
    version: int = 1
    enabled: bool = True


# --------------------------------------------------------------------------------------------------
# Rules and agents
# --------------------------------------------------------------------------------------------------


class Ruleset(Entity):
    """Instructions injected into the system prompt of every task where it applies."""

    collection: ClassVar[str] = "rulesets"

    name: str
    description: str = ""
    content: str  # markdown
    position: Literal["prepend", "append"] = "append"
    version: int = 1


class AgentTool(StrEnum):
    RUN_PYTHON = "run_python"
    RUN_HTML = "run_html"
    READ_ATTACHMENT = "read_attachment"
    WRITE_FILE = "write_file"


class AgentDef(Entity):
    """An agent: persona + sandbox tools + turn limit. Requires a tool-capable model."""

    collection: ClassVar[str] = "agents"

    name: str
    description: str = ""
    persona: str
    tools: list[AgentTool] = Field(default_factory=lambda: [AgentTool.RUN_PYTHON, AgentTool.WRITE_FILE])
    max_turns: int = 8
    tool_timeout_s: float = 30.0
    version: int = 1


# --------------------------------------------------------------------------------------------------
# Suites and runs
# --------------------------------------------------------------------------------------------------


class Suite(Entity):
    """Battery: which tasks x which models x which rules/agent x how many repetitions."""

    collection: ClassVar[str] = "suites"

    name: str
    description: str = ""
    task_ids: list[str] = Field(default_factory=list)
    model_ids: list[str] = Field(default_factory=list)
    ruleset_ids: list[str] = Field(default_factory=list)
    agent_id: str | None = None
    repetitions: int = Field(default=1, ge=1, le=50)
    concurrency: int = Field(default=4, ge=1, le=64)
    task_timeout_s: float = 900.0
    judge_enabled: bool = True


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"


class RunSnapshot(BaseModel):
    """Frozen copy of everything that defines the run (P5 reproducibility)."""

    tasks: list[Task]
    models: list[ModelProfile]
    rulesets: list[Ruleset] = Field(default_factory=list)
    agent: AgentDef | None = None
    judge: ModelProfile | None = None


class Run(Entity):
    """An execution of a battery (full or partial)."""

    collection: ClassVar[str] = "runs"

    name: str
    suite_id: str | None = None
    status: RunStatus = RunStatus.PENDING
    snapshot: RunSnapshot
    repetitions: int = 1
    concurrency: int = 4
    task_timeout_s: float = 900.0
    judge_enabled: bool = True
    # Re-run: a run can repeat another one fully or only a subset of tasks/models/results.
    rerun_of: str | None = None
    rerun_scope: Literal["full", "tasks", "results"] | None = None
    rerun_items: list[str] = Field(default_factory=list)  # selected task slugs / model names / result ids
    snapshot_mode: Literal["original", "current"] = "original"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    total_items: int = 0
    done_items: int = 0
    error: str | None = None
    notes: str = ""


class ResultStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    REFUSED = "refused"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"  # e.g. a vision task on a model without vision


class Metrics(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    reasoning_estimated: bool = False  # backfilled as output − visible answer (older runs)
    cached_tokens: int = 0
    latency_ms: float = 0.0
    ttft_ms: float | None = None
    output_tokens_per_s: float | None = None
    cost_usd: float = 0.0
    retries: int = 0
    turns: int = 1
    tool_calls: int = 0


class CheckResult(BaseModel):
    check_id: str
    name: str
    dimension: Literal["intelligence", "conformity"]
    passed: bool
    score: float = 0.0  # 0..1 (a check may give partial credit)
    weight: float = 1.0
    skipped: bool = False  # not evaluable here (e.g. no browser): excluded from scoring
    detail: str = ""
    duration_ms: float = 0.0


class JudgeCriterion(BaseModel):
    name: str
    score: float
    comment: str = ""


class JudgeVerdict(BaseModel):
    judge_model_id: str
    judge_model_name: str
    score: float  # 1..10
    criteria: list[JudgeCriterion] = Field(default_factory=list)
    rationale: str = ""
    same_model_warning: bool = False
    metrics: Metrics = Field(default_factory=Metrics)
    error: str | None = None


class Score(BaseModel):
    auto: float | None = None  # 0..10 from checks
    judge: float | None = None  # 1..10 judge preview
    user: float | None = None  # 1..10 final user score (wins)
    user_comment: str = ""
    reviewed: bool = False

    @property
    def final(self) -> float | None:
        if self.user is not None:
            return self.user
        if self.judge is not None and self.auto is not None:
            return round((self.judge + self.auto) / 2, 2)
        return self.judge if self.judge is not None else self.auto


class Result(Entity):
    """Result of (task x model x repetition) within a run."""

    collection: ClassVar[str] = "results"

    run_id: str
    task_id: str
    task_slug: str
    task_version: int
    model_id: str
    model_name: str
    repetition: int = 0
    status: ResultStatus = ResultStatus.PENDING
    response_text: str = ""
    reasoning_text: str = ""
    transcript: list[dict[str, Any]] = Field(default_factory=list)  # agent turns
    finish_reason: str | None = None
    error: str | None = None
    metrics: Metrics = Field(default_factory=Metrics)
    checks: list[CheckResult] = Field(default_factory=list)
    judge: JudgeVerdict | None = None
    score: Score = Field(default_factory=Score)
    artifact_ids: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def key(self) -> str:
        """Pairing key across runs: same task and same model."""
        return f"{self.task_slug}::{self.model_name}"


class Artifact(Entity):
    """File produced by a model. Content lives in the ArtifactStore; this is metadata only."""

    collection: ClassVar[str] = "artifacts"

    run_id: str
    result_id: str
    name: str
    kind: ArtifactKind
    size: int
    sha256: str
    storage: Literal["local", "firebase"] = "local"
    location: str  # local path or blob path
    mime: str = "text/plain"


class ExecutionRecord(BaseModel):
    """Output of a sandbox execution (checks, replay, agent tools)."""

    ok: bool
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    timed_out: bool = False
    isolation: Literal["docker", "process"] = "process"
    files: dict[str, str] = Field(default_factory=dict)  # collected (text) output files
    screenshot_b64: str | None = None


ENTITY_TYPES: tuple[type[Entity], ...] = (
    ProviderConnection,
    ModelProfile,
    Task,
    Ruleset,
    AgentDef,
    Suite,
    Run,
    Result,
    Artifact,
)
