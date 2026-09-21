"""Conditional recall, actual lane overlap and cancellation, without network calls."""

import asyncio
from threading import Event

import pytest

from agent_memory.models import Evidence, Memory, QueryPlan, Session
from agent_memory.retrieval import Retriever
from agent_memory.settings import Settings


def fact(identifier="a", **changes):
    values = {
        "memory_id": identifier,
        "session_id": "s",
        "scope_id": "s",
        "subject": "项目",
        "predicate": "截止日期",
        "value": "9月20日",
        "content": "项目截止日期是9月20日",
        "evidence": [Evidence(turn_id="t", event_index=0, quote="9月20日")],
    }
    return Memory(**(values | changes))


class Planner:
    def __init__(self, **changes):
        self.value = QueryPlan(subject="项目", predicate="截止日期").model_copy(update=changes)

    async def plan(self, query, context):
        return self.value


class Embedder:
    def __init__(self):
        self.inputs = []

    async def encode(self, texts):
        self.inputs.append(texts)
        return [[1.0, 0.0] for _ in texts]


def test_exact_lookup_skips_embedding_and_keeps_local_override():
    async def run():
        embedder = Embedder()
        retriever = Retriever(Settings(), Planner(), embedder)
        short = fact(value="9月21日", content="项目截止日期改为9月21日")
        long = fact("long", tier="long", scope_type="user", scope_id="owner")
        try:
            result = await retriever.search("项目的截止日期是什么", Session(session_id="s"), [short], [long])
            assert not embedder.inputs
            assert {h.metadata["memory_id"] for h in result.results} == {"a", "long"}
            assert result.results[0].metadata["local_override"]
            for channel in result.channels:
                assert channel.query_conditions["query_type"] == "field_lookup"
                assert channel.query_conditions["execution_mode"] == "local_batch"
                if channel.view == "semantic":
                    assert channel.status == "skipped" and channel.detail == "exact_field_lookup"
                    assert channel.elapsed_ms == channel.input_count == channel.matched_count == 0
                else:
                    assert channel.status == "complete" and channel.elapsed_ms > 0
        finally:
            retriever.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("query", "changes", "kind"),
    [
        ("项目截止日期有什么影响", {}, "open"),
        ("比较项目截止日期", {}, "multi_info"),
        ("项目截止日期", {"required_info": ["截止日期", "预算"]}, "multi_info"),
        ("项目截止日期", {"response_intent": "action_plan"}, "multi_info"),
        ("项目截止日期", {"semantic_queries": ["时间", "预算"]}, "multi_info"),
        ("项目历史安排", {"temporal_mode": "history"}, "temporal"),
    ],
)
def test_open_multi_info_and_temporal_queries_use_available_routes(query, changes, kind):
    async def run():
        embedder = Embedder()
        retriever = Retriever(Settings(), Planner(**changes), embedder)
        try:
            result = await retriever.search(query, Session(session_id="s"), [fact()], [])
            assert len(embedder.inputs) == 1
            assert result.results[0].metadata["matched_views"] == ["lexical", "semantic", "symbolic"]
            assert all(c.status == "complete" for c in result.channels[:3])
            assert all(c.query_conditions["query_type"] == kind for c in result.channels[:3])
            assert all(c.query_conditions["execution_mode"] == "parallel" for c in result.channels[:3])
        finally:
            retriever.close()

    asyncio.run(run())


def test_field_label_miss_restores_semantics_without_crossing_scope():
    async def run():
        embedder = Embedder()
        retriever = Retriever(Settings(), Planner(predicate="交付日期"), embedder)
        try:
            result = await retriever.search(
                "项目交付日期是什么",
                Session(session_id="s"),
                [fact(), fact("private", session_id="other", scope_id="other", content="secret")],
                [],
            )
            assert len(embedder.inputs) == 1 and "secret" not in embedder.inputs[0]
            assert [h.metadata["memory_id"] for h in result.results] == ["a"]
            assert "semantic" in result.results[0].metadata["matched_views"]
            assert "symbolic_field_miss: fell back to scoped recall" in result.warnings
        finally:
            retriever.close()

    asyncio.run(run())


def test_comparison_does_not_lose_other_fields_when_planner_supplies_only_one():
    async def run():
        retriever = Retriever(Settings(), Planner(), Embedder())
        try:
            result = await retriever.search(
                "比较项目截止日期与预算",
                Session(session_id="s"),
                [fact(), fact("budget", predicate="预算", value="1000", content="项目预算为1000")],
                [],
            )
            assert {h.metadata["memory_id"] for h in result.results} == {"a", "budget"}
            assert (
                "multi_slot_recall: symbolic labels do not restrict other required fields" in result.warnings
            )
        finally:
            retriever.close()

    asyncio.run(run())


def test_three_routes_overlap_and_fusion_is_deterministic():
    async def run():
        entered = {view: Event() for view in ("lexical", "symbolic", "semantic")}

        def rendezvous(view):
            entered[view].set()
            assert all(event.wait(2) for event in entered.values()), "recall routes did not overlap"

        class ConcurrentRetriever(Retriever):
            def _lexical_recall(self, *args):
                rendezvous("lexical")
                return super()._lexical_recall(*args)

            def _symbolic_recall(self, *args):
                rendezvous("symbolic")
                return super()._symbolic_recall(*args)

        class ConcurrentEmbedder(Embedder):
            async def encode(self, texts):
                await asyncio.to_thread(rendezvous, "semantic")
                return await super().encode(texts)

        retriever = ConcurrentRetriever(
            Settings(), Planner(required_info=["日期", "原因"]), ConcurrentEmbedder()
        )
        records = [fact("b"), fact("a")]
        try:
            first = await retriever.search("项目安排", Session(session_id="s"), records, [])
            for event in entered.values():
                event.clear()
            second = await retriever.search("项目安排", Session(session_id="s"), records[::-1], [])
            assert [h.model_dump() for h in first.results] == [h.model_dump() for h in second.results]
            assert [h.metadata["memory_id"] for h in first.results] == ["a", "b"]
            assert all(
                h.metadata["matched_views"] == ["lexical", "semantic", "symbolic"] for h in first.results
            )
        finally:
            retriever.close()

    asyncio.run(run())


@pytest.mark.parametrize("fail_local", [False, True])
def test_deadline_or_lane_failure_cancels_embedding_and_releases_request(fail_local):
    async def run():
        started, cancelled = Event(), asyncio.Event()

        class WaitingEmbedder(Embedder):
            async def encode(self, texts):
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()

        class FailingRetriever(Retriever):
            def _lexical_recall(self, *args):
                if fail_local:
                    assert started.wait(2)
                    raise RuntimeError("local lane failed")
                return super()._lexical_recall(*args)

        retriever = FailingRetriever(Settings(), Planner(), WaitingEmbedder())
        try:
            with pytest.raises(RuntimeError if fail_local else TimeoutError):
                await retriever.search("项目时间影响", Session(session_id="s"), [fact()], [], timeout=0.2)
            assert cancelled.is_set()
            assert not retriever._projection_locks.get("default", asyncio.Lock()).locked()
            assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        finally:
            retriever.close()

    asyncio.run(run())


def test_repeated_semantic_queries_are_encoded_once():
    async def run():
        embedder = Embedder()
        retriever = Retriever(Settings(), Planner(semantic_queries=["时间", " 时间 ", ""]), embedder)
        try:
            await retriever.search("项目安排", Session(session_id="s"), [fact()], [])
            assert embedder.inputs == [["时间", "项目截止日期是9月20日"]]
        finally:
            retriever.close()

    asyncio.run(run())


@pytest.mark.parametrize("semantic", [False, True])
def test_no_lexical_terms_does_not_claim_local_execution(semantic):
    async def run():
        retriever = Retriever(
            Settings(), Planner(subject=None, predicate=None), Embedder() if semantic else None
        )
        try:
            result = await retriever.search("?", Session(session_id="s"), [fact()], [])
            lexical, symbolic, vector = result.channels[:3]
            assert lexical.status == symbolic.status == "skipped"
            assert lexical.detail == "no_lexical_terms"
            assert lexical.elapsed_ms == symbolic.elapsed_ms == 0
            assert vector.query_conditions["execution_mode"] == ("single" if semantic else "skipped")
            assert vector.status == ("complete" if semantic else "disabled")
        finally:
            retriever.close()

    asyncio.run(run())
