"""Isolated SDK-shape tests; these are NOT official host validate_plugin checks."""

import asyncio
import importlib.util
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import pytest

from agent_memory.models import AnswerResponse, Hit, QueryPlan, Scope, SearchResponse


@pytest.fixture
def bridge(monkeypatch):
    """Install a test-only fake in sys.modules, restored by monkeypatch at teardown."""

    @dataclass
    class EngineCapabilities:
        supports_ingest: bool = False
        supports_delete: bool = False
        supports_generate: bool = True
        supports_stream: bool = True
        supports_browse: bool = False
        supported_suffixes: list[str] = field(default_factory=list)
        ingest_granularity: str = "file"
        storage_backend: str = ""

    class MemoryEnginePlugin(ABC):
        @property
        @abstractmethod
        def capabilities(self): ...

        @abstractmethod
        async def check_availability(self): ...

        @abstractmethod
        async def search(self, query, top_k=10, timeout=30.0): ...

    server = ModuleType("server")
    server.__path__ = []
    engines = ModuleType("server.engines")
    engines.__path__ = []
    sdk = ModuleType("server.engines.memory_plugin_api")
    sdk.EngineCapabilities = EngineCapabilities
    sdk.MemoryEnginePlugin = MemoryEnginePlugin
    for name, value in [("server", server), ("server.engines", engines), (sdk.__name__, sdk)]:
        monkeypatch.setitem(sys.modules, name, value)
    path = Path(__file__).parents[1] / "src/agent_memory/integrations/rag_sdk.py"
    # A unique test module name leaves the real optional import untouched.
    name = "agent_memory.integrations._test_sdk_bridge"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


class FakeRuntime:
    def __init__(self, error=None):
        self.error = error
        self.calls = []
        self.status = "ok"

    async def health(self):
        return {"status": self.status}

    async def search(self, scope, session_id, query, top_k=10, timeout=30.0):
        await asyncio.sleep(0)
        self.calls.append((scope.owner_id, session_id, query, top_k, timeout))
        if self.error:
            raise self.error
        hits = (
            [
                Hit(
                    content=f"{scope.owner_id}:{session_id}",
                    score=0.8,
                    source_file=f"[memory:{session_id}]",
                    chunk_id=f"{session_id}:1",
                )
            ]
            if top_k > 0
            else []
        )
        return SearchResponse(
            results=hits,
            plan=QueryPlan(),
            mode="test",
            candidate_count=len(hits),
            selected_k=len(hits),
            context_tokens=10,
        )

    async def answer(self, scope, session_id, query, top_k=10, timeout=30.0):
        result = await self.search(scope, session_id, query, top_k, timeout)
        return AnswerResponse(
            generated_text="Based on the retrieved memory [1].",
            citations=[1],
            sources=result.results,
            retrieval_count=len(result.results),
            elapsed_ms=1,
        )


def test_explicit_configuration_and_availability(bridge):
    async def scenario():
        assert await bridge.engine_plugin.check_availability() is False
        with (
            bridge.bind_request(Scope(tenant_id="t", owner_id="a"), "s"),
            pytest.raises(RuntimeError, match="Configure"),
        ):
            await bridge.engine_plugin.search("query")
        runtime = FakeRuntime()
        assert bridge.configure(runtime) is bridge.engine_plugin
        assert await bridge.engine_plugin.check_availability() is True
        runtime.status = "unavailable"
        assert await bridge.engine_plugin.check_availability() is False
        caps = bridge.engine_plugin.capabilities
        assert caps.supports_generate and caps.supports_stream
        assert not caps.supports_ingest and not caps.supports_delete and not caps.supports_browse
        assert caps.supported_suffixes == []
        with pytest.raises(ValueError):
            bridge.configure(None)

    asyncio.run(scenario())


def test_context_required_and_nested_binding_resets(bridge):
    bridge.configure(FakeRuntime())

    async def scenario():
        with pytest.raises(PermissionError):
            await bridge.engine_plugin.search("query")
        with pytest.raises(PermissionError):
            await bridge.engine_plugin.generate("query")
        outer = Scope(tenant_id="t", owner_id="a")
        with bridge.bind_request(outer, "session-a"):
            outer.owner_id = "changed-after-binding"
            assert bridge.require_context().scope.owner_id == "a"
            with bridge.bind_request(Scope(tenant_id="t", owner_id="b"), "session-b"):
                assert bridge.require_context().scope.owner_id == "b"
            assert bridge.require_context().session_id == "session-a"
        with pytest.raises(PermissionError):
            bridge.require_context()
        with pytest.raises(ValueError), bridge.bind_request(outer, " "):
            pass

    asyncio.run(scenario())


def test_concurrent_scope_isolation_and_result_contract(bridge):
    runtime = FakeRuntime()
    bridge.configure(runtime)

    async def run(owner):
        with bridge.bind_request(Scope(tenant_id="tenant", owner_id=owner), f"session-{owner}"):
            hits = await bridge.engine_plugin.search("query", top_k=2, timeout=3.0)
            assert len(hits) == 1
            assert set(hits[0]) == {"content", "score", "source_file", "chunk_id", "engine", "metadata"}
            assert hits[0]["content"] == f"{owner}:session-{owner}"
            assert 0 <= hits[0]["score"] <= 1
            assert hits[0]["engine"] == "simple_memory"

    async def scenario():
        await asyncio.gather(run("a"), run("b"))
        assert {call[:2] for call in runtime.calls} == {("a", "session-a"), ("b", "session-b")}
        assert all(call[3:] == (2, 3.0) for call in runtime.calls)

    asyncio.run(scenario())


def test_generate_and_buffered_stream_contract(bridge):
    bridge.configure(FakeRuntime())

    async def scenario():
        with bridge.bind_request(Scope(tenant_id="t", owner_id="a"), "s"):
            result = await bridge.engine_plugin.generate("query")
            assert {
                "generated_text",
                "citations",
                "sources",
                "retrieval_count",
                "elapsed_ms",
            } <= result.keys()
            assert result["sources"][0]["source_file"] == "[memory:s]"
            events = [event async for event in bridge.engine_plugin.generate_stream("query")]
        assert [name for name, _ in events] == [
            "engine_start",
            "engine_status",
            "engine_token",
            "engine_done",
        ]
        assert events[2][1]["delta"] == result["generated_text"]
        done = events[-1][1]
        assert done["status"] == "ok"
        assert {
            "engine",
            "engine_label",
            "engine_color",
            "generated_text",
            "citations",
            "sources",
            "elapsed_ms",
            "retrieval_count",
            "status",
        } <= done.keys()

    asyncio.run(scenario())


def test_failure_propagates_for_search_generate_and_terminates_stream_once(bridge):
    bridge.configure(FakeRuntime(error=RuntimeError("backend internal details")))

    async def scenario():
        with bridge.bind_request(Scope(tenant_id="t", owner_id="a"), "s"):
            with pytest.raises(RuntimeError, match="backend"):
                await bridge.engine_plugin.search("query")
            with pytest.raises(RuntimeError, match="backend"):
                await bridge.engine_plugin.generate("query")
            events = [event async for event in bridge.engine_plugin.generate_stream("query")]
        done = [payload for name, payload in events if name == "engine_done"]
        assert len(done) == 1
        assert done[0]["status"] == "error"
        assert done[0]["generated_text"] == ""
        assert "internal details" not in str(events)

    asyncio.run(scenario())


def test_stream_cancellation_propagates_without_fake_done(bridge):
    bridge.configure(FakeRuntime(error=asyncio.CancelledError()))

    async def scenario():
        events = []
        with (
            bridge.bind_request(Scope(tenant_id="t", owner_id="a"), "s"),
            pytest.raises(asyncio.CancelledError),
        ):
            async for event in bridge.engine_plugin.generate_stream("query"):
                events.append(event)
        assert all(name != "engine_done" for name, _ in events)

    asyncio.run(scenario())


def test_search_timeout_bounds_runtime_call(bridge):
    class SlowRuntime(FakeRuntime):
        async def search(self, *args, **kwargs):
            await asyncio.sleep(1)

    bridge.configure(SlowRuntime())

    async def scenario():
        with (
            bridge.bind_request(Scope(tenant_id="t", owner_id="a"), "s"),
            pytest.raises(TimeoutError),
        ):
            await bridge.engine_plugin.search("query", timeout=0.001)

    asyncio.run(scenario())
