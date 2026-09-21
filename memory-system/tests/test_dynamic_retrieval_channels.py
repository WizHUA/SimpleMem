"""Adaptive retrieval budgets and truthful channel observations."""

import asyncio

from agent_memory.models import Evidence, Memory, QueryPlan, Session
from agent_memory.retrieval import Retriever
from agent_memory.settings import Settings


class Planner:
    def __init__(self, **values):
        self.plan_value = QueryPlan(**values)

    async def plan(self, query, context):
        return self.plan_value


class Embedder:
    async def encode(self, texts):
        return [[1.0, 0.0] for _ in texts]


def memories(count):
    return [
        Memory(
            memory_id=f"m{i}",
            session_id="s",
            scope_id="s",
            subject="项目",
            predicate="信息",
            value=str(i),
            content=f"项目信息{i}",
            evidence=[Evidence(turn_id="t", event_index=0, quote="项目")],
        )
        for i in range(count)
    ]


def run_search(plan, count=20, embedder=None, **kwargs):
    async def run():
        retriever = Retriever(Settings(retrieval_token_limit=20000), Planner(**plan), embedder)
        events = []

        async def progress(phase, detail, **data):
            events.append(data)

        try:
            result = await retriever.search(
                "项目信息", Session(session_id="s"), memories(count), [], progress=progress, **kwargs
            )
            return result, events
        finally:
            retriever.close()

    return asyncio.run(run())


def test_default_adapts_above_ten_but_explicit_cap_is_respected():
    full, _ = run_search({"depth": 12})
    capped, _ = run_search({"depth": 12}, top_k=5)
    assert full.selected_k == full.dynamic_k.target_k == 12
    assert full.dynamic_k.safety_cap == 20
    assert full.dynamic_k.candidate_limit == 72
    assert capped.selected_k == capped.dynamic_k.target_k == 5
    assert full.dynamic_k.score_semantics == "relevance_not_truth_confidence"


def test_required_fields_raise_depth_and_evidence_count_does_not_pad():
    result, _ = run_search({"depth": 3, "required_info": [f"项目{i}" for i in range(9)]}, count=2)
    assert result.dynamic_k.target_k == 9
    assert result.selected_k == 2
    assert result.dynamic_k.used_tokens == result.context_tokens


def test_disabled_semantics_and_missing_symbolic_labels_are_honest():
    result, events = run_search({"depth": 3})
    channels = {(c.tier, c.view): c for c in result.channels}
    assert channels["short", "semantic"].status == "disabled"
    assert channels["short", "semantic"].matched_count == 0
    assert channels["short", "symbolic"].status == "skipped"
    assert channels["short", "lexical"].status == "complete"
    assert channels["short", "lexical"].matched_count > 0
    assert channels["short", "lexical"].selected_count == result.selected_k
    assert channels["long", "lexical"].status == "skipped"
    assert any(len(event.get("channels", [])) == 3 for event in events)
    assert any(len(event.get("channels", [])) == 6 for event in events)


def test_semantic_and_symbolic_counts_are_measured_not_inferred_from_final_k():
    result, _ = run_search(
        {"depth": 3, "subject": "项目", "predicate": "信息", "required_info": ["项目", "信息"]},
        count=8,
        embedder=Embedder(),
    )
    channels = {(c.tier, c.view): c for c in result.channels}
    for view in ("semantic", "symbolic"):
        assert channels["short", view].status == "complete"
        assert channels["short", view].input_count == 8
        assert channels["short", view].matched_count == 8
        assert channels["short", view].selected_count == 3


def test_empty_corpus_never_claims_embedding_execution():
    result, _ = run_search({"depth": 3}, count=0, embedder=Embedder())
    assert all(channel.status == "skipped" for channel in result.channels)
    assert all(channel.input_count == 0 for channel in result.channels)


def test_symbolic_miss_explains_actual_conditions_and_checked_candidates():
    result, _ = run_search({"depth": 3, "subject": "不存在的主体", "predicate": "不存在的属性"}, count=4)
    symbolic = next(c for c in result.channels if c.tier == "short" and c.view == "symbolic")
    assert symbolic.status == "complete" and symbolic.matched_count == 0
    assert symbolic.query_conditions["subject"] == "不存在的主体"
    assert symbolic.candidate_total == len(symbolic.candidates) == 4
    assert all(c.subject == "项目" and c.predicate == "信息" for c in symbolic.candidates)
    assert all(not c.matched and not c.selected and "不一致" in c.reason for c in symbolic.candidates)


def test_channel_previews_are_bounded_and_selected_identity_matches_sources():
    result, _ = run_search({"depth": 12}, count=50)
    lexical = next(c for c in result.channels if c.tier == "short" and c.view == "lexical")
    assert lexical.candidate_total == 50
    assert lexical.candidates_truncated and len(lexical.candidates) == 20
    selected = {(hit.metadata["memory_id"], hit.metadata["version"]) for hit in result.results}
    for candidate in lexical.candidates:
        assert candidate.selected == ((candidate.memory_id, candidate.version) in selected)


def test_inspected_previews_never_include_ineligible_records():
    from datetime import timedelta

    from agent_memory.models import utcnow

    async def run():
        retriever = Retriever(Settings(), Planner(depth=3))
        records = memories(5)
        records[1].status = "retracted"
        records[2].scope_id = "other-session"
        records[3].valid_to = utcnow() - timedelta(seconds=1)
        records[4].status = "pending"
        try:
            result = await retriever.search("项目信息", Session(session_id="s"), records, [])
            assert {
                candidate.memory_id for channel in result.channels for candidate in channel.candidates
            } == {"m0"}
        finally:
            retriever.close()

    asyncio.run(run())
