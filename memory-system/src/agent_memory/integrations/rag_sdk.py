"""Optional RAG SDK 1.0.0 bridge, requiring the real host SDK at import time.

Configure the singleton during host startup. Before scheduling any retrieval,
bind an authenticated scope and an authorized session using ``bind_request``.
The bridge does not authenticate caller-supplied user or session identifiers.

``generate_stream`` is buffered compatibility: it waits for a complete answer,
then emits one token event. It is not model token streaming. Ordinary stream
failures produce one error done event; cancelled/disconnected streams propagate
CancelledError without yielding further events.
"""

import asyncio
from collections.abc import AsyncGenerator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from server.engines.memory_plugin_api import EngineCapabilities, MemoryEnginePlugin

from ..models import AnswerResponse, Scope, SearchResponse


class Runtime(Protocol):
    async def search(
        self, scope: Scope, session_id: str, query: str, top_k: int = 10, timeout: float = 30.0
    ) -> SearchResponse: ...

    async def answer(
        self, scope: Scope, session_id: str, query: str, top_k: int = 10, timeout: float = 30.0
    ) -> AnswerResponse: ...

    async def health(self) -> dict: ...


@dataclass(frozen=True)
class RequestContext:
    scope: Scope
    session_id: str


_request_context: ContextVar[RequestContext | None] = ContextVar("agent_memory_request", default=None)


@contextmanager
def bind_request(scope: Scope, session_id: str) -> Iterator[RequestContext]:
    """Bind host-verified identity; establish this before creating child tasks."""
    if not session_id.strip():
        raise ValueError("An authorized session_id is required")
    context = RequestContext(scope=scope.model_copy(deep=True), session_id=session_id)
    token = _request_context.set(context)
    try:
        yield context
    finally:
        _request_context.reset(token)


def require_context() -> RequestContext:
    context = _request_context.get()
    if context is None:
        raise PermissionError("Bind an authenticated memory request context before invoking the SDK")
    return context


class SimpleMemoryEngine(MemoryEnginePlugin):
    name = "simple_memory"
    engine_label = "长短期记忆"
    engine_color = "#2563eb"
    version = "0.1.0"
    description = "Independent short and long-term memory with intent-aware retrieval"
    contract_version = "1.0.0"

    def __init__(self) -> None:
        self._runtime: Runtime | None = None

    def configure(self, runtime: Runtime) -> None:
        """Wire one shared runtime during startup, never per request."""
        if runtime is None:
            raise ValueError("A configured memory runtime is required")
        self._runtime = runtime

    @property
    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            supports_ingest=False,
            supports_delete=False,
            supports_generate=True,
            supports_stream=True,
            supports_browse=False,
            supported_suffixes=[],
            ingest_granularity="file",
            storage_backend="runtime-managed",
        )

    def _require_runtime(self) -> Runtime:
        if self._runtime is None:
            raise RuntimeError("Configure the memory runtime during host startup")
        return self._runtime

    async def check_availability(self) -> bool:
        if self._runtime is None:
            return False
        # Do not permanently cache readiness: storage/model availability can recover.
        async with asyncio.timeout(5.0):
            health = await self._runtime.health()
        return health.get("status") == "ok"

    async def search(self, query: str, top_k: int = 10, timeout: float = 30.0) -> list[dict]:
        context = require_context()
        runtime = self._require_runtime()
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        async with asyncio.timeout(timeout):
            response = await runtime.search(
                context.scope, context.session_id, query, top_k=top_k, timeout=timeout
            )
        return [hit.model_dump(mode="json") for hit in response.results]

    async def generate(self, query: str, top_k: int = 10, timeout: float = 30.0) -> dict:
        context = require_context()
        runtime = self._require_runtime()
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        async with asyncio.timeout(timeout):
            answer = await runtime.answer(
                context.scope, context.session_id, query, top_k=top_k, timeout=timeout
            )
        return answer.model_dump(mode="json")

    async def generate_stream(
        self, query: str, top_k: int = 10, timeout: float = 30.0
    ) -> AsyncGenerator[tuple[str, dict], None]:
        started = perf_counter()
        identity = {
            "engine": self.name,
            "engine_label": self.engine_label,
            "engine_color": self.engine_color,
        }
        yield "engine_start", dict(identity)
        yield "engine_status", {"engine": self.name, "phase": "buffered_generation"}
        try:
            remaining = timeout - (perf_counter() - started)
            if remaining <= 0:
                raise TimeoutError("Generation timeout exhausted")
            answer = await self.generate(query, top_k=top_k, timeout=remaining)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the stream contract requires one terminal error event
            # Match the stream termination protocol without exposing exception text.
            yield (
                "engine_done",
                {
                    **identity,
                    "status": "error",
                    "generated_text": "",
                    "citations": [],
                    "sources": [],
                    "elapsed_ms": int((perf_counter() - started) * 1000),
                    "retrieval_count": 0,
                },
            )
            return
        if answer["generated_text"]:
            yield "engine_token", {"engine": self.name, "delta": answer["generated_text"]}
        yield (
            "engine_done",
            {
                **identity,
                **answer,
                "status": "degraded" if answer.get("warnings") else "ok",
                "elapsed_ms": int((perf_counter() - started) * 1000),
            },
        )


engine_plugin = SimpleMemoryEngine()


def configure(runtime: Runtime) -> SimpleMemoryEngine:
    """Convenience hook for the exported plugin singleton."""
    engine_plugin.configure(runtime)
    return engine_plugin
