"""Practical entity joins must retain the evidence, permissions and time boundary."""

import unittest
from datetime import timedelta

from agent_memory.models import Evidence, Memory, QueryPlan, Session, utcnow
from agent_memory.retrieval import Retriever, _Match
from agent_memory.settings import Settings


def fact(identifier, subject, predicate, value, **changes):
    data = {
        "memory_id": identifier,
        "session_id": "old-session",
        "scope_type": "user",
        "scope_id": "owner",
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "content": f"{subject}的{predicate}是{value}",
        "tier": "long",
        "evidence": [Evidence(turn_id="t1", event_index=0, quote=f"{subject} {value}")],
    }
    data.update(changes)
    return Memory(**data)


class EntityRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def search(self, query, items, **kwargs):
        return await Retriever(Settings()).search(
            query, Session(session_id="new-session"), [], items, **kwargs
        )

    async def test_alias_can_retrieve_canonical_person_fact_with_source(self):
        items = [fact("alias", "王晨", "昵称", "bluebird"), fact("food", "王晨", "饮食", "素食")]
        result = await self.search("bluebird饮食是什么", items)
        self.assertEqual({h.metadata["memory_id"] for h in result.results}, {"alias", "food"})
        food = next(h for h in result.results if h.metadata["memory_id"] == "food")
        self.assertEqual(food.metadata["support_memory_ids"], ["alias"])
        self.assertIn("entity_link", food.metadata["matched_views"])
        self.assertTrue(any(s.action == "evidence_entity_expansion" for s in result.steps))

    async def test_two_hop_query_selects_whole_relation_chain(self):
        items = [
            fact("lead", "曙光项目", "负责人", "孙岚"),
            fact("team", "孙岚", "团队", "北辰组"),
            fact("office", "北辰组", "办公地点", "R210"),
            fact("noise", "南方组", "办公地点", "R999"),
        ]
        result = await self.search("曙光项目负责人的团队办公地点在哪", items, top_k=3)
        self.assertEqual({h.metadata["memory_id"] for h in result.results}, {"lead", "team", "office"})
        office = next(h for h in result.results if h.metadata["memory_id"] == "office")
        self.assertEqual(office.metadata["support_memory_ids"], ["lead", "team"])

    async def test_chain_is_not_returned_without_room_for_support(self):
        result = await self.search(
            "bluebird饮食",
            [fact("alias", "王晨", "昵称", "bluebird"), fact("food", "王晨", "饮食", "素食")],
            top_k=1,
        )
        self.assertFalse(any(h.metadata["support_memory_ids"] for h in result.results))

    async def test_expired_inferred_and_other_scope_aliases_do_not_create_links(self):
        for changes in (
            {"valid_to": utcnow() - timedelta(days=1)},
            {"assertion": "hypothetical"},
            {"assertion": "inferred"},
            {"status": "retracted"},
            {"status": "pending"},
            {"status": "archived"},
            {"scope_type": "session", "scope_id": "old-session"},
            {"scope_type": "project", "scope_id": "secret-project"},
        ):
            items = [
                fact("alias", "王晨", "昵称", "bluebird", **changes),
                fact("food", "王晨", "饮食", "素食"),
            ]
            result = await self.search("bluebird饮食", items)
            self.assertFalse(
                any("entity_link" in h.metadata["matched_views"] for h in result.results), changes
            )

    async def test_english_alias_is_not_a_substring_match(self):
        result = await self.search(
            "bluebirdxx饮食",
            [fact("alias", "王晨", "昵称", "bluebird"), fact("food", "王晨", "饮食", "素食")],
        )
        self.assertFalse(any("entity_link" in h.metadata["matched_views"] for h in result.results))

    async def test_hierarchy_path_is_a_recall_hint_not_a_permission_override(self):
        result = await self.search(
            "基础设施",
            [
                fact("db", "项目", "数据库", "Postgres", hierarchy_path=["研发", "基础设施"]),
                fact(
                    "secret",
                    "项目",
                    "数据库",
                    "MySQL",
                    hierarchy_path=["基础设施"],
                    scope_type="project",
                    scope_id="other",
                ),
            ],
        )
        self.assertEqual([h.metadata["memory_id"] for h in result.results], ["db"])
        self.assertIn("hierarchy", result.results[0].metadata["matched_views"])

    def test_bm25_rewards_rare_terms_and_penalizes_unrelated_verbosity(self):
        scores = Retriever._bm25({"project", "quasar"}, ["project " * 20, "quasar", "project", "project"])
        self.assertGreater(scores[1], scores[0])
        compact, verbose = Retriever._bm25({"quasar"}, ["quasar", "quasar " + "noise " * 100])
        self.assertGreater(compact, verbose)

    async def test_entity_expansion_does_not_mutate_memories(self):
        items = [fact("alias", "王晨", "昵称", "bluebird"), fact("food", "王晨", "饮食", "素食")]
        before = [m.model_dump() for m in items]
        await self.search("bluebird饮食", items)
        self.assertEqual(before, [m.model_dump() for m in items])

    async def test_full_candidate_pool_keeps_relevant_chain_under_cap(self):
        items = [
            fact("alias", "王晨", "昵称", "bluebird"),
            fact("food", "王晨", "饮食", "素食"),
            *[fact(f"noise{i}", f"路人{i}", "饮食", "无特殊要求") for i in range(80)],
        ]
        result = await self.search("bluebird饮食", items, top_k=3)
        ids = {h.metadata["memory_id"] for h in result.results}
        self.assertTrue({"alias", "food"}.issubset(ids))
        self.assertLessEqual(result.candidate_count, 18)

    def test_age_decay_is_bounded_and_preserves_stable_and_historical_facts(self):
        now = utcnow()
        for kind in ("fact", "preference"):
            hit = _Match(
                fact("old", "用户", "偏好", "素食", kind=kind, recorded_at=now - timedelta(days=3650)), 0.8
            )
            self.assertEqual(Retriever._rank_score(hit, QueryPlan(), now), 0.8)
        hit = _Match(
            fact("old", "用户", "行程", "会议", kind="event", recorded_at=now - timedelta(days=3650)), 0.8
        )
        decayed = Retriever._rank_score(hit, QueryPlan(), now)
        self.assertLess(decayed, 0.8)
        self.assertGreaterEqual(decayed, 0.8 * 0.95)
        self.assertEqual(Retriever._rank_score(hit, QueryPlan(temporal_mode="history"), now), 0.8)

    async def test_source_budget_never_exposes_leaf_without_bridge(self):
        items = [fact("alias", "王晨", "昵称", "bluebird"), fact("food", "王晨", "饮食", "素食")]
        result = await Retriever(Settings(retrieval_token_limit=120)).search(
            "bluebird饮食", Session(session_id="new-session"), [], items
        )
        ids = {h.metadata["memory_id"] for h in result.results}
        for hit in result.results:
            self.assertTrue(set(hit.metadata["support_memory_ids"]).issubset(ids))

    async def test_pending_contradiction_warns_without_becoming_evidence(self):
        items = [
            fact("old", "王晨", "饮食", "素食"),
            fact("pending", "王晨", "饮食", "荤食", status="pending"),
            fact("unrelated", "李山", "饮食", "清淡", status="pending"),
            fact(
                "private",
                "王晨",
                "饮食",
                "秘密偏好",
                status="pending",
                scope_type="project",
                scope_id="other",
            ),
        ]
        result = await self.search("王晨饮食是什么", items)
        self.assertEqual([h.metadata["memory_id"] for h in result.results], ["old"])
        self.assertEqual(
            [c["memory_id"] for c in result.results[0].metadata["conflict_pending"]], ["pending"]
        )
        warnings = " ".join(result.warnings)
        self.assertIn("pending_conflict_requires_confirmation", warnings)
        self.assertNotIn("秘密偏好", warnings)
        self.assertNotIn("清淡", warnings)
