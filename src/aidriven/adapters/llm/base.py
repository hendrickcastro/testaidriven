"""Shared helpers for LLM adapters: timing, retries, refusal heuristics."""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from aidriven.domain.models import ProviderConnection
from aidriven.ports import ConnectionTest, DiscoveredModel, LLMError, LLMRequest, LLMResponse, Message

log = logging.getLogger(__name__)
T = TypeVar("T")

# Heuristic for providers that don't signal refusals explicitly. Only checked on short answers.
_REFUSAL_RE = re.compile(
    r"^\s*(I'm sorry|I am sorry|I can(?:'|no)t help|I can(?:'|no)t assist|I won't|I will not|"
    r"Lo siento|No puedo ayudar)",
    re.IGNORECASE,
)


def looks_like_refusal(text: str) -> bool:
    return len(text) < 600 and bool(_REFUSAL_RE.search(text))


class Stopwatch:
    """Measures total latency and time-to-first-token."""

    def __init__(self) -> None:
        self.t0 = time.perf_counter()
        self.first: float | None = None

    def mark_first(self) -> None:
        if self.first is None:
            self.first = time.perf_counter()

    @property
    def ttft_ms(self) -> float | None:
        return (self.first - self.t0) * 1000 if self.first is not None else None

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.t0) * 1000


async def with_retries(
    fn: Callable[[], Awaitable[T]], *, max_retries: int, is_transient: Callable[[Exception], bool]
) -> tuple[T, int]:
    """Run `fn` retrying transient failures with jittered exponential backoff. Returns (value, retries)."""
    attempt = 0
    while True:
        try:
            return await fn(), attempt
        except Exception as exc:
            if attempt >= max_retries or not is_transient(exc):
                raise
            delay = min(2.0 * (2**attempt) + random.uniform(0, 1), 30.0)
            log.warning("transient LLM error (%s), retry %d/%d in %.1fs", exc, attempt + 1, max_retries, delay)
            await asyncio.sleep(delay)
            attempt += 1


class BaseAdapter:
    """Common behaviour. Subclasses implement `_generate` and `list_models`."""

    def __init__(self, connection: ProviderConnection, api_key: str | None) -> None:
        self.connection = connection
        self.api_key = api_key

    async def generate(self, request: LLMRequest) -> LLMResponse:
        try:
            response, retries = await with_retries(
                lambda: self._generate(request),
                max_retries=self.connection.max_retries,
                is_transient=self._is_transient,
            )
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"{type(exc).__name__}: {exc}", transient=self._is_transient(exc)) from exc
        response.retries = retries
        return response

    async def _generate(self, request: LLMRequest) -> LLMResponse:  # pragma: no cover - abstract
        raise NotImplementedError

    def _is_transient(self, exc: Exception) -> bool:
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None) or getattr(exc, "code", None)
        if isinstance(status, int):
            return status in (408, 409, 425, 429) or status >= 500
        name = type(exc).__name__
        return any(k in name for k in ("Timeout", "Connection", "RateLimit", "ServiceUnavailable", "Overloaded"))

    async def list_models(self) -> list[DiscoveredModel]:  # pragma: no cover - abstract
        raise NotImplementedError

    async def test_connection(self, model: str | None = None) -> ConnectionTest:
        """Cheap check: list models; if a model is given, also a tiny generation."""
        sw = Stopwatch()
        try:
            models = await self.list_models()
            detail = f"{len(models)} models available"
            if model:
                from aidriven.domain.models import GenerationParams

                resp = await self.generate(
                    LLMRequest(
                        model=model,
                        messages=[Message(role="user", text="Reply with the single word: pong")],
                        params=GenerationParams(max_output_tokens=256, stream=False),
                    )
                )
                detail += (
                    f"; {model} answered {resp.text.strip()[:40]!r} ({resp.input_tokens}+{resp.output_tokens} tok)"
                )
            return ConnectionTest(ok=True, latency_ms=sw.elapsed_ms(), detail=detail)
        except Exception as exc:
            return ConnectionTest(ok=False, latency_ms=sw.elapsed_ms(), detail=f"{type(exc).__name__}: {exc}"[:500])
