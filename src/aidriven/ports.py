"""Ports (hexagonal architecture, constitution P2). Adapters live in `aidriven.adapters`."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from aidriven.domain.models import Entity, ExecutionRecord, GenerationParams, ModelProfile, ProviderConnection

E = TypeVar("E", bound=Entity)

# --------------------------------------------------------------------------------------------------
# LLM
# --------------------------------------------------------------------------------------------------


@dataclass
class ImagePart:
    mime: str
    data_b64: str


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    text: str = ""
    images: list[ImagePart] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)  # assistant turns that call tools
    tool_call_id: str | None = None  # tool turns: which call this answers
    tool_name: str | None = None
    provider_raw: Any = None  # provider-native assistant content, replayed verbatim in agent loops


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema


@dataclass
class LLMRequest:
    model: str
    messages: list[Message]
    params: GenerationParams
    system: str = ""
    tools: list[ToolSpec] = field(default_factory=list)
    # Runner context (task slug, run id...). Real adapters ignore it; the fake oracle uses it.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str = ""
    reasoning_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: float = 0.0
    ttft_ms: float | None = None
    finish_reason: str | None = None
    refusal: bool = False
    model_reported: str | None = None
    raw_usage: dict[str, Any] = field(default_factory=dict)
    provider_raw: Any = None  # assistant content to replay in multi-turn tool loops
    retries: int = 0


class LLMError(Exception):
    def __init__(self, message: str, *, transient: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.transient = transient
        self.status = status


@dataclass
class ConnectionTest:
    ok: bool
    latency_ms: float
    detail: str


@dataclass
class DiscoveredModel:
    id: str
    label: str = ""
    context_window: int | None = None
    max_output_tokens: int | None = None
    vision: bool | None = None
    input_per_mtok: float | None = None
    output_per_mtok: float | None = None
    created: int | None = None  # release timestamp (epoch s) when the provider reports it


class LLMPort(Protocol):
    """One adapter instance per ProviderConnection."""

    connection: ProviderConnection

    async def generate(self, request: LLMRequest) -> LLMResponse: ...

    async def test_connection(self, model: str | None = None) -> ConnectionTest: ...

    async def list_models(self) -> list[DiscoveredModel]: ...


# --------------------------------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------------------------------


@runtime_checkable
class RepositoryPort(Protocol):
    """Document repository. Only single-field equality filters (no composite indexes needed)."""

    backend: str

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


# --------------------------------------------------------------------------------------------------
# Artifacts
# --------------------------------------------------------------------------------------------------


class ArtifactStorePort(Protocol):
    kind: Literal["local", "firebase"]

    def put(self, run_id: str, result_id: str, name: str, content: bytes, mime: str) -> str:
        """Store content and return its location (path or blob path)."""
        ...

    def get(self, location: str) -> bytes: ...

    def delete(self, location: str) -> None: ...


# --------------------------------------------------------------------------------------------------
# Sandbox
# --------------------------------------------------------------------------------------------------


@dataclass
class SandboxJob:
    files: dict[str, bytes]  # workdir content (name -> bytes)
    command: list[str]  # e.g. ["python", "_check.py"]; "python" resolves to the sandbox interpreter
    timeout_s: float = 60.0
    needs_browser: bool = False  # use the playwright image
    collect: list[str] = field(default_factory=list)  # output files to return (text) or "_screenshot.png"


class SandboxPort(Protocol):
    @property
    def isolation(self) -> Literal["docker", "process"]: ...

    async def run(self, job: SandboxJob) -> ExecutionRecord: ...

    def available(self) -> bool: ...


# --------------------------------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------------------------------


class SecretStorePort(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


# --------------------------------------------------------------------------------------------------
# Misc
# --------------------------------------------------------------------------------------------------

ProgressCallback = Callable[[str, dict[str, Any]], Awaitable[None] | None]
TokenStream = AsyncIterator[str]


def profile_label(profile: ModelProfile) -> str:
    return f"{profile.name} ({profile.model})"
