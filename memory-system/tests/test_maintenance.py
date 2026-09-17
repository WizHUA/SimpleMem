"""Practical lifecycle cases: aliases, corrections, drift, recovery and provenance."""

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from agent_memory.long_term import LongTermMemory
from agent_memory.maintenance import correction_relation, retention, summary_groups
from agent_memory.models import Candidate, Event, Evidence, EvolutionInput, Scope, TurnInput, utcnow
from agent_memory.store import SQLiteStore


class MaintenanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.temp.name) / "memory.sqlite")
        self.scope = Scope(tenant_id="t", owner_id="alice")
        self.session = self.store.create_session(self.scope, "别名测试", None)
        self.service = LongTermMemory(self.store)
        self.counter = 0

    async def asyncTearDown(self):
        self.store.close()
        self.temp.cleanup()

    def memory(self, value="飞猪", quote=None, **changes):
        self.counter += 1
        quote = quote or f"zfc 的昵称是{value}。"
        turn = self.store.append_turn(
            self.scope,
            self.session.session_id,
            TurnInput(
                request_id=f"turn-{self.counter}",
                events=[Event(role="user", content=quote)],
            ),
        )
        item = Candidate(
            subject="zfc",
            predicate="昵称",
            value=value,
            content=quote,
            durable=True,
            scope_type="user",
            evidence=[Evidence(turn_id=turn.turn_id, event_index=0, quote=quote)],
            **changes,
        )
        return self.store.add_candidates(self.scope, self.session.session_id, [item])[0]

    async def test_durable_statement_promotes_and_wording_duplicate_merges_evidence(self):
        first = self.memory()
        report = await self.service.process_extraction(self.scope, [first])
        self.assertEqual(report["applied"][0]["action"], "promote")
        second = self.memory(quote="请记住，zfc 的昵称是飞猪。")
        report = await self.service.process_extraction(self.scope, [second])
        self.assertEqual(report["applied"][0]["action"], "merge")
        current = self.store.get_memory(self.scope, first.memory_id)
        self.assertEqual(len(current.evidence), 2)
        self.assertEqual(self.store.get_memory(self.scope, second.memory_id).status, "superseded")

    async def test_unexplained_conflicting_value_is_pending(self):
        first = self.memory()
        await self.service.process_extraction(self.scope, [first])
        second = self.memory("大象")
        result = await self.service.process_extraction(self.scope, [second])
        self.assertEqual(result["review"][0]["reason"], "conflicting_values_require_review")
        self.assertEqual(self.store.get_memory(self.scope, second.memory_id).status, "pending")
        self.assertEqual(self.store.get_memory(self.scope, first.memory_id).value, "飞猪")

    async def test_actual_change_preserves_old_validity_and_new_version(self):
        first = self.memory(valid_from=utcnow() - timedelta(days=1))
        await self.service.process_extraction(self.scope, [first])
        second = self.memory("大象", "zfc 的昵称从飞猪改为大象。")
        report = await self.service.process_extraction(self.scope, [second])
        self.assertEqual(report["applied"][0]["action"], "supersede")
        current = self.store.get_memory(self.scope, first.memory_id)
        self.assertEqual((current.value, current.version), ("大象", 2))
        versions = [m for m in self.store.memories(self.scope) if m.memory_id == first.memory_id]
        self.assertEqual(versions[0].valid_to, current.valid_from)

    async def test_correction_retracts_erroneous_assertion_instead_of_pretending_historical_truth(self):
        first = self.memory()
        await self.service.process_extraction(self.scope, [first])
        second = self.memory("大象", "更正：zfc 不是飞猪而是大象，之前说错了。")
        report = await self.service.process_extraction(self.scope, [second])
        self.assertEqual(report["applied"][0]["action"], "correct")
        old = next(
            m for m in self.store.memories(self.scope) if m.memory_id == first.memory_id and m.version == 1
        )
        self.assertEqual(old.status, "retracted")

    async def test_question_or_hypothesis_never_authorizes_correction(self):
        first = self.memory()
        second = first.model_copy(update={"value": "大象"})
        for quote in ("zfc 从飞猪改为大象了吗？", "假如 zfc 从飞猪改为大象。"):
            self.assertIsNone(correction_relation(second, first, [quote]))

    async def test_expired_event_archives_without_destroying_evidence_and_can_restore(self):
        first = self.memory(kind="event", valid_to=utcnow() - timedelta(days=1))
        await self.service.process_extraction(self.scope, [first])
        result = await self.service.maintain(self.scope)
        self.assertEqual(result["applied"][0]["action"], "archive")
        archived = self.store.get_memory(self.scope, first.memory_id)
        self.assertEqual(archived.status, "archived")
        restored = self.store.evolve(
            self.scope,
            archived.memory_id,
            EvolutionInput(
                action="restore",
                expected_version=archived.version,
            ),
        )
        self.assertEqual(restored.status, "active")
        self.assertEqual(restored.evidence, first.evidence)
        self.assertEqual(summary_groups([restored], utcnow()), [])

    async def test_old_stable_fact_only_gets_archive_recommendation(self):
        memory = self.memory().model_copy(update={"recorded_at": utcnow() - timedelta(days=900)})
        decision = retention(memory, utcnow())
        self.assertEqual(decision["recommendation"], "review_archive")
        self.assertLess(decision["strength"], 0.25)

    async def test_summary_is_derived_and_every_item_cites_exact_version(self):
        memory = self.memory()
        await self.service.process_extraction(self.scope, [memory])
        report = await self.service.maintain(self.scope)
        group = report["groups"][0]
        self.assertFalse(group["usable_as_extraction_evidence"])
        self.assertEqual(group["summary_items"][0]["memory_ref"], f"{memory.memory_id}:1")
        self.assertIn("飞猪", group["summary"])
        self.assertEqual(self.store.groups(self.scope)[0]["summary"], group["summary"])

    async def test_model_summary_failure_keeps_extracts_and_extractive_projection(self):
        class BrokenModel:
            async def summarize(self, memories):
                raise RuntimeError("offline")

        memory = self.memory()
        await self.service.process_extraction(self.scope, [memory])
        self.service.model = BrokenModel()
        report = await self.service.maintain(self.scope)
        self.assertTrue(report["warnings"])
        self.assertTrue(report["groups"][0]["summary"])
        self.assertEqual(self.store.get_memory(self.scope, memory.memory_id).status, "active")

    async def test_model_cannot_publish_foreign_summary_sources(self):
        class ForeignModel:
            async def summarize(self, memories):
                return {"summary": "伪造", "source_refs": ["other-owner:1"]}

        memory = self.memory()
        await self.service.process_extraction(self.scope, [memory])
        self.service.model = ForeignModel()
        report = await self.service.maintain(self.scope)
        self.assertTrue(report["warnings"])
        self.assertNotIn("synthesis", self.store.groups(self.scope)[0])

    async def test_summaries_exist_at_every_level_and_cache_only_unchanged_sources(self):
        class SummaryModel:
            calls = 0

            async def summarize(self, memories):
                self.calls += 1
                return {
                    "summary": "zfc 的昵称为飞猪。",
                    "source_refs": [f"{memories[0].memory_id}:{memories[0].version}"],
                }

        memory = self.memory()
        await self.service.process_extraction(self.scope, [memory])
        model = SummaryModel()
        self.service.model = model
        first = await self.service.maintain(self.scope)
        self.assertEqual({g["level"] for g in first["groups"]}, {1, 2, 3})
        self.assertEqual(first["semantic_synthesis"], "model_assisted_with_sources")
        self.assertEqual(model.calls, 3)
        await self.service.maintain(self.scope)
        self.assertEqual(model.calls, 3)
        self.store.evolve(self.scope, memory.memory_id, EvolutionInput(action="retract", expected_version=1))
        self.assertEqual(self.store.groups(self.scope), [])

    async def test_model_relation_advice_cannot_overwrite_unresolved_conflict(self):
        class RelationModel:
            async def relate(self, source, targets):
                return {
                    "relation": "correction",
                    "target_id": targets[0].memory_id,
                    "reason": "新说法与旧说法不同",
                    "evidence_quotes": [source.evidence[0].quote],
                }

        first = self.memory()
        await self.service.process_extraction(self.scope, [first])
        self.service.model = RelationModel()
        second = self.memory("大象")
        result = await self.service.process_extraction(self.scope, [second])
        self.assertFalse(result["review"][0]["automatically_applied"])
        self.assertEqual(self.store.get_memory(self.scope, first.memory_id).value, "飞猪")
        self.assertEqual(self.store.get_memory(self.scope, second.memory_id).status, "pending")
