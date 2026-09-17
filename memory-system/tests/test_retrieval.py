"""Deterministic contract checks; no model server, database or network required."""

import asyncio
import unittest
from datetime import timedelta

from agent_memory.models import Evidence, Memory, QueryPlan, Session, utcnow
from agent_memory.providers import ModelOutputError
from agent_memory.retrieval import Retriever, estimate_tokens
from agent_memory.settings import Settings

SESSION = Session(session_id="s1", project_id="p1")


def memory(identifier: str, **changes) -> Memory:
    fields = {
        "memory_id": identifier,
        "session_id": "s1",
        "scope_id": "s1",
        "subject": "项目P",
        "predicate": "截止日期",
        "value": "9月12日",
        "content": "项目P的截止日期为9月12日",
        "scope_type": "session",
        "evidence": [Evidence(turn_id="t1", event_index=0, quote="9月12日截止")],
    }
    fields.update(changes)
    return Memory(**fields)


class Planner:
    def __init__(self, **fields):
        self.result = QueryPlan(**fields)

    async def plan(self, query, context):
        return self.result


class Embedder:
    async def encode(self, texts):
        return [[1.0, 0.0] for _ in texts]


class BrokenProvider:
    async def plan(self, query, context):
        raise RuntimeError("provider unavailable")

    async def encode(self, texts):
        raise RuntimeError("provider unavailable")


class MalformedPlanner:
    async def plan(self, query, context):
        raise ModelOutputError("invalid planner JSON")


class TrackingRetriever(Retriever):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.searched = []

    async def _recall(self, query, plan, session, memories, limit, now, warnings):
        self.searched.append([m.memory_id for m in memories])
        return await super()._recall(query, plan, session, memories, limit, now, warnings)


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_model_plan_falls_back_to_rule_planner(self):
        result = await Retriever(Settings(), MalformedPlanner()).search(
            "项目P 截止日期", SESSION, [memory("recent")], []
        )
        self.assertEqual(result.plan.route, "both")
        self.assertIn("model_planner_schema_error: fell back to rule planner", result.warnings)

    async def test_dynamic_depth_controls_candidates_and_final_count(self):
        items = [memory(f"m{i:02}", tier="long", scope_type="user", scope_id="owner") for i in range(60)]
        settings = Settings(retrieval_token_limit=20000)
        small = await Retriever(settings, Planner(route="long", depth=3)).search(
            "项目P 截止日期", SESSION, [], items, top_k=20
        )
        large = await Retriever(settings, Planner(route="long", depth=8)).search(
            "项目P 截止日期", SESSION, [], items, top_k=20
        )
        limited = await Retriever(settings, Planner(route="long", depth=8)).search(
            "项目P 截止日期", SESSION, [], items, top_k=2
        )
        self.assertEqual((small.selected_k, large.selected_k, limited.selected_k), (3, 8, 2))
        self.assertEqual((small.candidate_count, large.candidate_count), (18, 48))
        self.assertEqual(small.mode, "lexical_baseline")
        self.assertEqual(
            [step.phase for step in small.steps],
            ["planning", "short_retrieval", "long_retrieval", "filter", "selection"],
        )
        self.assertEqual(small.steps[-1].output_count, small.selected_k)

    async def test_exact_recent_field_stays_in_stm(self):
        retriever = TrackingRetriever(Settings())
        result = await retriever.search(
            "刚才项目P的截止日期是什么", SESSION, [memory("recent")], [memory("old", tier="long")]
        )
        self.assertEqual(result.plan.route, "short")
        self.assertEqual(retriever.searched, [["recent"]])
        self.assertEqual(result.results[0].metadata["memory_id"], "recent")

    async def test_uncertain_stm_uses_one_long_lookup(self):
        retriever = TrackingRetriever(
            Settings(), Planner(route="short", depth=3, required_info=["当前日期", "原因"])
        )
        result = await retriever.search("项目P 截止日期", SESSION, [memory("a")], [memory("b", tier="long")])
        self.assertEqual(len(retriever.searched), 2)
        self.assertTrue(any("coverage_unverified" in warning for warning in result.warnings))

    async def test_lower_ranked_stm_conflict_preserves_long_quota(self):
        short = [memory(f"a{i}") for i in range(25)] + [memory("z", value="9月13日")]
        retriever = TrackingRetriever(Settings())
        result = await retriever.search(
            "刚才项目P的截止日期是什么", SESSION, short, [memory("long", tier="long")]
        )
        self.assertEqual(len(retriever.searched), 2)
        self.assertIn(["long"], retriever.searched)
        self.assertLessEqual(result.candidate_count, 18)

    async def test_current_and_history_respect_valid_intervals(self):
        now = utcnow()
        items = [
            memory(
                "past",
                status="superseded",
                valid_from=now - timedelta(days=3),
                valid_to=now - timedelta(days=1),
            ),
            memory(
                "current",
                status="superseded",
                valid_from=now - timedelta(days=1),
                valid_to=now + timedelta(days=1),
            ),
            memory("future", valid_from=now + timedelta(days=1)),
            memory("wrong", status="retracted"),
            memory("pending", status="pending"),
        ]
        current = await Retriever(Settings(), Planner(route="long")).search(
            "项目P 截止日期", SESSION, [], items
        )
        history = await Retriever(Settings(), Planner(route="long", temporal_mode="history")).search(
            "项目P 截止日期", SESSION, [], items
        )
        as_of = await Retriever(Settings(), Planner(route="long", as_of=now - timedelta(days=2))).search(
            "项目P 截止日期", SESSION, [], items
        )
        self.assertEqual([h.metadata["memory_id"] for h in current.results], ["current"])
        self.assertEqual({h.metadata["memory_id"] for h in history.results}, {"past", "current", "future"})
        self.assertEqual([h.metadata["memory_id"] for h in as_of.results], ["past"])

    async def test_scopes_and_symbolic_filters_apply_before_recall(self):
        items = [
            memory("other_session", session_id="s2", scope_id="s2"),
            memory("other_project", scope_type="project", scope_id="p2"),
            memory("project", scope_type="project", scope_id="p1"),
            memory("other_field", predicate="计划", content="项目P截止日期相关计划"),
        ]
        result = await Retriever(
            Settings(), Planner(route="long", subject="项目P", predicate="截止日期")
        ).search("项目P 截止日期", SESSION, [], items)
        self.assertEqual([h.metadata["memory_id"] for h in result.results], ["project"])

    async def test_three_views_merge_the_same_version_once(self):
        item = memory("same")
        result = await Retriever(
            Settings(), Planner(route="both", subject="项目P", predicate="截止日期"), Embedder()
        ).search("项目P 截止日期", SESSION, [item, item], [item])
        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(result.selected_k, 1)
        self.assertEqual(result.mode, "hybrid")
        self.assertEqual(result.results[0].metadata["matched_views"], ["lexical", "semantic", "symbolic"])
        self.assertGreaterEqual(result.results[0].score, 0)
        self.assertLessEqual(result.results[0].score, 1)

    async def test_current_local_override_is_visible_without_mutation(self):
        local = memory("local", predicate="输出语言", value="英文", content="项目P这次输出语言使用英文")
        durable = memory(
            "durable",
            predicate="输出语言",
            value="中文",
            content="项目P输出语言默认中文",
            tier="long",
            scope_type="user",
            scope_id="owner",
        )
        before = [m.model_dump(mode="json") for m in (local, durable)]
        result = await Retriever(Settings(), Planner(route="long")).search(
            "项目P 输出语言", SESSION, [local], [durable], top_k=1
        )
        self.assertEqual(result.results[0].metadata["memory_id"], "local")
        self.assertTrue(result.results[0].metadata["local_override"])
        self.assertEqual(before, [m.model_dump(mode="json") for m in (local, durable)])

    async def test_no_match_and_no_budget_do_not_produce_sources(self):
        retriever = Retriever(Settings())
        for query, top_k in (("银河星系", 10), ("", 10), ("项目P", 0)):
            result = await retriever.search(query, SESSION, [memory("a")], [], top_k=top_k)
            self.assertEqual(result.results, [])
            self.assertEqual(result.context_tokens, 0)
        too_small = await Retriever(Settings(retrieval_token_limit=10)).search(
            "项目P 截止日期", SESSION, [memory("a")], []
        )
        self.assertEqual(too_small.results, [])
        self.assertEqual(too_small.context_tokens, 0)
        self.assertEqual(estimate_tokens("中文 abc"), 6)

    async def test_configured_provider_failures_propagate_for_sdk_circuit_breaker(self):
        provider = BrokenProvider()
        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            await Retriever(Settings(), provider).search("项目P 截止日期", SESSION, [memory("a")], [])
        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            await Retriever(Settings(), embedder=provider).search(
                "项目P 截止日期", SESSION, [memory("a")], []
            )

    async def test_total_deadline_cancels_upstream_work(self):
        class StuckPlanner:
            async def plan(self, query, context):
                await asyncio.Future()

        with self.assertRaises(TimeoutError):
            await Retriever(Settings(), StuckPlanner()).search("项目P", SESSION, [], [], timeout=0.01)


if __name__ == "__main__":
    unittest.main()
