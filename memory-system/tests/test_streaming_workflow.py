"""HTTP stream exercises real extraction, storage, query and version checks."""

import json

from fastapi.testclient import TestClient

from agent_memory.api import create_app
from agent_memory.models import Candidate, Evidence, ExtractionResult, QueryPlan
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


class Model:
    async def extract(self, window):
        candidates = []
        for turn in window.new_turns:
            for index, event in enumerate(turn.events):
                if event.role == "user" and event.content == "北斗的昵称是小熊。":
                    candidates.append(
                        Candidate(
                            content=event.content,
                            subject="北斗",
                            predicate="别名",
                            value="小熊",
                            evidence=[Evidence(turn_id=turn.turn_id, event_index=index, quote=event.content)],
                        )
                    )
        return ExtractionResult(candidates=candidates, summary="北斗的昵称是小熊")

    async def plan(self, query, context):
        return QueryPlan(route="both", keywords=["北斗"], depth=3)

    async def answer(self, prompt):
        assert "小熊" in prompt
        return "北斗的昵称是小熊。【来源1】"


def test_stream_has_real_ordered_stages_and_single_final_result(tmp_path):
    runtime = MemoryRuntime(Settings(db_path=tmp_path / "stream.db"), model=Model())
    with TestClient(create_app(runtime=runtime)) as client:
        sid = client.post("/api/v1/sessions", json={}).json()["session_id"]
        client.post(
            f"/api/v1/sessions/{sid}/turns",
            json={"request_id": "r1", "events": [{"role": "user", "content": "北斗的昵称是小熊。"}]},
        )
        result = client.post(
            f"/api/v1/sessions/{sid}/answer/stream", json={"session_id": sid, "query": "北斗叫什么？"}
        )
        assert result.status_code == 200
        events = [json.loads(line) for line in result.text.splitlines()]
        assert [e["phase"] for e in events if e["type"] == "stage"] == [
            "extraction",
            "planning",
            "planning",
            "retrieval",
            "retrieval",
            "retrieval",
            "generation",
            "completed",
        ]
        assert len([e for e in events if e["type"] == "result"]) == 1
        answer = events[-1]["answer"]
        assert answer["sources"][0]["metadata"]["evidence"][0]["quote"] == "北斗的昵称是小熊。"
        assert answer["memory_updates"][0]["candidate_count"] == 1
        stages = [e for e in events if e["type"] == "stage"]
        assert stages[2]["plan"] == answer["plan"]
        assert stages[-3]["sources"] == answer["sources"]
        assert stages[-3]["steps"] == answer["query_steps"]
        assert answer["sources"][0]["metadata"]["recorded_at"]
        saved = client.post(
            f"/api/v1/sessions/{sid}/turns",
            json={
                "request_id": "reply",
                "events": [
                    {
                        "role": "assistant",
                        "content": answer["generated_text"],
                        "answer_id": answer["answer_id"],
                    }
                ],
            },
        )
        assert saved.status_code == 201
        context = saved.json()["turn"]["events"][0]["answer_context"]
        assert context["sources"] == answer["sources"]
        assert [e["phase"] for e in context["run_events"]] == [e["phase"] for e in stages]
        assert client.get(f"/api/v1/sessions/{sid}").json()["pending_turns"] == 1
    runtime.retriever.close()
    runtime.store.close()


def test_stream_failure_is_terminal_and_does_not_fake_completion(tmp_path):
    runtime = MemoryRuntime(Settings(db_path=tmp_path / "error.db"))
    with TestClient(create_app(runtime=runtime)) as client:
        sid = client.post("/api/v1/sessions", json={}).json()["session_id"]
        response = client.post(
            f"/api/v1/sessions/{sid}/answer/stream", json={"session_id": sid, "query": "你好"}
        )
        events = [json.loads(line) for line in response.text.splitlines()]
        assert len(events) == 1 and events[0]["type"] == "error"
        assert events[0]["status"] == 503
        assert (
            client.post(
                f"/api/v1/sessions/{sid}/answer/stream", json={"session_id": "other", "query": "你好"}
            ).status_code
            == 400
        )
    runtime.retriever.close()
    runtime.store.close()
