"""Real ASGI/SQLite delivery flows with a deterministic, offline model fixture."""

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from agent_memory.api import create_app
from agent_memory.models import Candidate, Evidence, ExtractionResult, QueryPlan, utcnow
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


class DeliveryModel:
    def __init__(self):
        self.answer_calls = 0
        self.pause_plan = False
        self.pause_answer = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def plan(self, query, context):
        if self.pause_plan:
            self.entered.set()
            await self.release.wait()
        return QueryPlan(route="long", semantic_queries=[query], subject="报告", predicate="语言")

    async def extract(self, window):
        candidates = []
        for turn in window.new_turns:
            content = turn.events[0].content
            candidates.append(
                Candidate(
                    content=content,
                    subject="报告",
                    predicate="语言",
                    value=content,
                    scope_type="project",
                    durable=True,
                    evidence=[Evidence(turn_id=turn.turn_id, event_index=0, quote=content)],
                )
            )
        return ExtractionResult(candidates=candidates, summary="报告语言约束")

    async def answer(self, prompt):
        self.answer_calls += 1
        if self.pause_answer:
            self.entered.set()
            await self.release.wait()
        evidence = prompt.split("检索证据：\n", 1)[1].split("当前问题：", 1)[0].strip()
        return evidence or "没有可用证据。"


@asynccontextmanager
async def service(path, *, key=""):
    settings = Settings(
        db_path=path / "delivery.sqlite3",
        api_key=key,
        deployment_mode="local",
        local_tenant="delivery-test",
        local_owner="alice",
    )
    model = DeliveryModel()
    runtime = MemoryRuntime(settings, model=model)
    app = create_app(settings, runtime)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://delivery.test"
            ) as client,
        ):
            yield client, model
    finally:
        await runtime.close()


async def session(client, project="report"):
    response = await client.post("/api/v1/sessions", json={"project_id": project})
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


async def extract(client, sid, text, request_id):
    payload = {"request_id": request_id, "events": [{"role": "user", "content": text}]}
    response = await client.post(f"/api/v1/sessions/{sid}/turns", json=payload)
    assert response.status_code == 201, response.text
    repeated = await client.post(f"/api/v1/sessions/{sid}/turns", json=payload)
    assert repeated.json()["turn"]["turn_id"] == response.json()["turn"]["turn_id"]
    response = await client.post(f"/api/v1/sessions/{sid}/extract?apply_evolution=false")
    assert response.status_code == 200, response.text
    return response.json()["memories"][0]


async def evolve(client, memory, action, **extra):
    response = await client.post(
        f"/api/v1/memories/{memory['memory_id']}/evolve",
        json={"action": action, "expected_version": memory["version"], **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_http_complete_lifecycle_and_cache_invalidation(tmp_path):
    async def scenario():
        async with service(tmp_path) as (client, model):
            source = await session(client)
            old = await extract(client, source, "报告语言使用中文。", "initial")
            old = await evolve(client, old, "promote")
            reader = await session(client)
            query = {"session_id": reader, "query": "报告语言"}
            search = await client.post("/api/v1/search", json=query)
            assert search.status_code == 200
            assert any("中文" in hit["content"] for hit in search.json()["results"])
            first = await client.post("/api/v1/answer", json=query)
            second = await client.post("/api/v1/answer", json=query)
            assert first.status_code == second.status_code == 200
            assert first.json()["citations"] == [1]
            assert second.json()["acceleration"]["cache_hit"]
            assert model.answer_calls == 1

            updated = await extract(client, source, "报告语言改为日语。", "correction")
            updated = await evolve(
                client,
                updated,
                "supersede",
                target_id=old["memory_id"],
                target_version=old["version"],
                effective_at=utcnow().isoformat(),
            )
            current = await client.post("/api/v1/answer", json=query)
            assert current.status_code == 200, current.text
            assert "日语" in current.json()["generated_text"]
            assert "中文" not in str(current.json()["sources"])
            assert not current.json()["acceleration"]["cache_hit"]
            assert model.answer_calls == 2
            history = await client.get(f"/api/v1/memories/{updated['memory_id']}/history")
            assert history.status_code == 200 and history.json()

            await evolve(client, updated, "retract")
            removed = await client.post("/api/v1/search", json=query)
            assert removed.status_code == 200 and removed.json()["results"] == []
            empty_answer = await client.post("/api/v1/answer", json=query)
            assert empty_answer.status_code == 200
            assert empty_answer.json()["sources"] == []
            assert "日语" not in empty_answer.json()["generated_text"]
            assert model.answer_calls == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("endpoint", ["search", "answer"])
def test_http_retraction_during_query_never_publishes_revoked_snapshot(tmp_path, endpoint):
    async def scenario():
        async with service(tmp_path) as (client, model):
            source = await session(client)
            memory = await extract(client, source, "报告语言使用中文。", "initial")
            memory = await evolve(client, memory, "promote")
            reader = await session(client)
            model.pause_plan = endpoint == "search"
            model.pause_answer = endpoint == "answer"
            query = {"session_id": reader, "query": "报告语言"}
            task = asyncio.create_task(client.post(f"/api/v1/{endpoint}", json=query))
            try:
                await asyncio.wait_for(model.entered.wait(), timeout=3)
                await evolve(client, memory, "retract")
            finally:
                model.release.set()
            response = await asyncio.wait_for(task, timeout=3)
            assert response.status_code == 409, response.text
            assert response.json()["error"] == "ConflictError"
            assert "中文" not in response.text
            fresh = await client.post("/api/v1/search", json=query)
            assert fresh.status_code == 200 and fresh.json()["results"] == []

    asyncio.run(scenario())


def test_http_authentication_and_project_scope(tmp_path):
    async def scenario():
        async with service(tmp_path, key="offline-delivery-key") as (client, _):
            denied = await client.post("/api/v1/sessions", json={})
            assert denied.status_code == 401
            assert denied.headers["WWW-Authenticate"] == "Bearer"
            client.headers["Authorization"] = "Bearer wrong"
            assert (await client.get("/api/v1/memories")).status_code == 401
            client.headers["Authorization"] = "Bearer offline-delivery-key"
            source = await session(client)
            memory = await extract(client, source, "报告语言使用中文。", "initial")
            await evolve(client, memory, "promote")
            other = await session(client, "other-project")
            result = await client.post(
                "/api/v1/search",
                json={"session_id": other, "query": "报告语言"},
                headers={"X-Owner-Id": "alice", "X-Tenant-Id": "delivery-test"},
            )
            assert result.status_code == 200 and result.json()["results"] == []
            client.headers.pop("Authorization")
            for method, path, payload in [
                ("GET", f"/api/v1/memories/{memory['memory_id']}/history", None),
                ("POST", "/api/v1/search", {"session_id": source, "query": "报告语言"}),
                ("DELETE", "/api/v1/owner/data", None),
            ]:
                response = await client.request(method, path, json=payload)
                assert response.status_code == 401

    asyncio.run(scenario())
