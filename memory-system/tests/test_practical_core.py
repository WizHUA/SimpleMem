"""Runtime-level regressions for realistic speech acts and partial failures."""

import asyncio
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from agent_memory.models import (
    Candidate,
    Event,
    Evidence,
    EvolutionInput,
    ExtractionResult,
    QueryPlan,
    Scope,
    TurnInput,
    utcnow,
)
from agent_memory.ports import ConflictError, NotFoundError
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


class ScriptedModel:
    def __init__(self):
        self.extractor = lambda window: ExtractionResult()
        self.extract_delay = 0
        self.plan_delay = 0
        self.windows = []
        self.answer_calls = 0

    async def extract(self, window):
        self.windows.append([t.sequence for t in window.new_turns])
        await asyncio.sleep(self.extract_delay)
        return self.extractor(window)

    async def plan(self, query, context):
        await asyncio.sleep(self.plan_delay)
        return QueryPlan(keywords=["zfc"], required_info=["昵称"])

    async def answer(self, prompt):
        self.answer_calls += 1
        return "依据已提供的记忆回答。"


class PracticalCoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.model = ScriptedModel()
        self.runtime = MemoryRuntime(
            Settings(
                db_path=Path(self.temp.name) / "memory.sqlite",
                model_timeout=0.2,
            ),
            model=self.model,
        )
        self.scope = Scope(tenant_id="t", owner_id="alice")
        self.session = self.runtime.store.create_session(self.scope, "实际会话", None)
        self.index = 0

    async def asyncTearDown(self):
        await self.runtime.close()
        self.temp.cleanup()

    async def append(self, text, role="user"):
        self.index += 1
        return await self.runtime.append_turn(
            self.scope,
            self.session.session_id,
            TurnInput(
                request_id=str(self.index),
                events=[Event(role=role, content=text)],
            ),
        )

    def extract_value(self, value, *, durable=False, quote=None, valid_to=None):
        def extract(window):
            turn = window.new_turns[-1]
            return ExtractionResult(
                candidates=[
                    Candidate(
                        content=f"zfc 的昵称是{value}",
                        subject="zfc",
                        predicate="昵称",
                        value=value,
                        durable=durable,
                        scope_type="user" if durable else "session",
                        valid_to=valid_to,
                        evidence=[
                            Evidence(
                                turn_id=turn.turn_id, event_index=0, quote=quote or turn.events[0].content
                            )
                        ],
                    )
                ]
            )

        self.model.extractor = extract

    def watermark(self):
        return self.runtime.store.get_session(self.scope, self.session.session_id).processed_sequence

    async def test_question_trimmed_to_affirmative_substring_cannot_become_memory(self):
        await self.append("zfc是飞猪吗")
        self.extract_value("飞猪", durable=True, quote="zfc是飞猪")
        with self.assertRaises(ValueError):
            await self.runtime.answer(self.scope, self.session.session_id, "zfc是飞猪吗")
        self.assertEqual(self.watermark(), 0)
        self.assertEqual(self.runtime.store.memories(self.scope), [])
        self.assertEqual(self.model.answer_calls, 0)

    async def test_assistant_guess_plus_user_question_cannot_launder_fact(self):
        await self.append("zfc是飞猪吗？")
        await self.append("是的，zfc是飞猪。", role="assistant")

        def extract(window):
            return ExtractionResult(
                candidates=[
                    Candidate(
                        content="zfc是飞猪",
                        subject="zfc",
                        predicate="昵称",
                        value="飞猪",
                        evidence=[
                            Evidence(turn_id=t.turn_id, event_index=0, quote="zfc是飞猪")
                            for t in window.new_turns
                        ],
                    )
                ]
            )

        self.model.extractor = extract
        with self.assertRaises(ValueError):
            await self.runtime.extract(self.scope, self.session.session_id)
        self.assertEqual(self.watermark(), 0)
        self.assertEqual(self.runtime.store.memories(self.scope), [])

    async def test_short_correction_uses_current_fact_and_preserves_retraction_audit(self):
        await self.append("zfc的昵称是飞猪。")
        self.extract_value("飞猪")
        await self.runtime.answer(self.scope, self.session.session_id, "zfc叫什么？")
        first = self.runtime.store.memories(self.scope)[0]
        await self.append("更正：zfc不是飞猪而是大象，之前说错了。")
        self.extract_value("大象")
        await self.runtime.answer(self.scope, self.session.session_id, "zfc叫什么？")
        self.assertEqual(self.runtime.store.get_memory(self.scope, first.memory_id).status, "retracted")
        result = await self.runtime.search(self.scope, self.session.session_id, "zfc昵称")
        self.assertTrue(any("大象" in hit.content for hit in result.results))
        self.assertFalse(any(hit.metadata.get("memory_id") == first.memory_id for hit in result.results))
        self.assertTrue(self.runtime.store.history(self.scope, first.memory_id))

    async def test_short_real_change_closes_old_validity_at_source_event_time(self):
        await self.append("zfc的昵称是飞猪。")
        self.extract_value("飞猪")
        await self.runtime.extract(self.scope, self.session.session_id)
        first = self.runtime.store.memories(self.scope)[0]
        appended = await self.append("zfc的昵称从飞猪改为大象。")
        self.extract_value("大象")
        await self.runtime.extract(self.scope, self.session.session_id)
        old = self.runtime.store.get_memory(self.scope, first.memory_id)
        current = next(m for m in self.runtime.store.memories(self.scope) if m.status == "active")
        expected = appended["turn"].events[0].occurred_at
        self.assertEqual(old.valid_to, expected)
        self.assertEqual(current.valid_from, expected)

    async def test_long_backlog_is_processed_in_contiguous_batches_before_answer(self):
        for index in range(13):
            await self.append(f"第{index}项观察：今天需要核对资料。")
        await self.runtime.answer(self.scope, self.session.session_id, "需要核对什么？")
        self.assertEqual(self.model.windows, [list(range(1, 6)), list(range(6, 11)), [11, 12, 13]])
        self.assertEqual(self.watermark(), 13)
        self.assertEqual(self.model.answer_calls, 1)

    async def test_extractor_timeout_preserves_watermark_but_planner_timeout_keeps_committed_extraction(self):
        await self.append("zfc的昵称是飞猪。")
        self.extract_value("飞猪")
        self.model.extract_delay = 0.3
        with self.assertRaises(TimeoutError):
            await self.runtime.answer(self.scope, self.session.session_id, "zfc是谁", timeout=0.02)
        self.assertEqual(self.watermark(), 0)
        self.model.extract_delay = 0
        self.model.plan_delay = 0.3
        with self.assertRaises(TimeoutError):
            await self.runtime.answer(self.scope, self.session.session_id, "zfc是谁", timeout=0.05)
        self.assertEqual(self.watermark(), 1)
        self.assertEqual(len(self.runtime.store.memories(self.scope)), 1)

    async def test_failed_postcommit_evolution_is_reported_and_recoverable(self):
        await self.append("zfc的昵称是飞猪。")
        self.extract_value("飞猪", durable=True)
        with patch.object(self.runtime.long_term, "process_extraction", side_effect=ConflictError("retry")):
            result = await self.runtime.extract(self.scope, self.session.session_id)
        self.assertEqual(result["status"], "processed")
        self.assertEqual(self.watermark(), 1)
        await self.runtime.answer(self.scope, self.session.session_id, "zfc是谁")
        self.assertTrue(any(m.tier == "long" for m in self.runtime.store.memories(self.scope)))

    async def test_archive_restore_and_pending_activate_enforce_version_and_status(self):
        await self.append("zfc的昵称是飞猪。")
        self.extract_value("飞猪", durable=True)
        await self.runtime.extract(self.scope, self.session.session_id)
        memory = self.runtime.store.memories(self.scope)[0]
        with self.assertRaises(ConflictError):
            await self.runtime.long_term.evolve(
                self.scope,
                memory.memory_id,
                EvolutionInput(
                    action="archive",
                    expected_version=99,
                ),
            )
        archived = await self.runtime.long_term.evolve(
            self.scope,
            memory.memory_id,
            EvolutionInput(
                action="archive",
                expected_version=memory.version,
            ),
        )
        with self.assertRaises(ConflictError):
            await self.runtime.long_term.evolve(
                self.scope,
                memory.memory_id,
                EvolutionInput(
                    action="activate",
                    expected_version=archived.version,
                ),
            )
        restored = await self.runtime.long_term.evolve(
            self.scope,
            memory.memory_id,
            EvolutionInput(
                action="restore",
                expected_version=archived.version,
            ),
        )
        pending = await self.runtime.long_term.evolve(
            self.scope,
            memory.memory_id,
            EvolutionInput(
                action="defer",
                expected_version=restored.version,
            ),
        )
        active = await self.runtime.long_term.evolve(
            self.scope,
            memory.memory_id,
            EvolutionInput(
                action="activate",
                expected_version=pending.version,
            ),
        )
        self.assertEqual(active.status, "active")

    async def test_derived_group_read_excludes_naturally_expired_sources(self):
        await self.append("zfc的昵称在短暂活动有效期内是飞猪。")
        self.extract_value("飞猪", durable=True, valid_to=utcnow() + timedelta(seconds=0.1))
        await self.runtime.extract(self.scope, self.session.session_id)
        # Keep this check deterministic: no optional model synthesis network calls.
        await self.runtime.long_term.maintain(self.scope)
        self.assertTrue(self.runtime.store.groups(self.scope))
        await asyncio.sleep(0.12)
        self.assertEqual(self.runtime.store.groups(self.scope), [])

    async def test_evolution_outbox_survives_restart_after_postcommit_failure(self):
        await self.append("zfc的昵称是飞猪。")
        self.extract_value("飞猪", durable=True)
        with patch.object(self.runtime.long_term, "process_extraction", side_effect=ConflictError("retry")):
            result = await self.runtime.extract(self.scope, self.session.session_id)
        self.assertTrue(result["evolution"]["warnings"])
        settings = self.runtime.settings
        await self.runtime.close()
        self.runtime = MemoryRuntime(settings, model=self.model)
        result = await self.runtime.extract(self.scope, self.session.session_id)
        self.assertEqual(result["status"], "idle")
        self.assertEqual(self.watermark(), 1)
        self.assertEqual(len(self.model.windows), 1)
        self.assertTrue(any(m.tier == "long" for m in self.runtime.store.memories(self.scope)))
        self.assertEqual(self.runtime.store.evolution_jobs(self.scope, self.session.session_id), [])

    async def test_revision_rejects_stale_page_action_after_archive_restore_round_trip(self):
        await self.append("zfc的昵称是飞猪。")
        self.extract_value("飞猪", durable=True)
        await self.runtime.extract(self.scope, self.session.session_id)
        memory = self.runtime.store.memories(self.scope)[0]
        archived = await self.runtime.long_term.evolve(
            self.scope,
            memory.memory_id,
            EvolutionInput(
                action="archive",
                expected_version=memory.version,
                expected_revision=memory.revision,
            ),
        )
        restored = await self.runtime.long_term.evolve(
            self.scope,
            memory.memory_id,
            EvolutionInput(
                action="restore",
                expected_version=archived.version,
                expected_revision=archived.revision,
            ),
        )
        self.assertEqual(restored.version, memory.version)
        self.assertGreater(restored.revision, memory.revision)
        with self.assertRaises(ConflictError):
            await self.runtime.long_term.evolve(
                self.scope,
                memory.memory_id,
                EvolutionInput(
                    action="retract",
                    expected_version=memory.version,
                    expected_revision=memory.revision,
                ),
            )

    async def test_cross_owner_cannot_resume_someone_elses_evolution_jobs(self):
        await self.append("zfc的昵称是飞猪。")
        self.extract_value("飞猪", durable=True)
        with patch.object(self.runtime.long_term, "process_extraction", side_effect=ConflictError("retry")):
            await self.runtime.extract(self.scope, self.session.session_id)
        bob = Scope(tenant_id="t", owner_id="bob")
        with self.assertRaises(NotFoundError):
            self.runtime.store.evolution_jobs(bob, self.session.session_id)
        self.assertTrue(self.runtime.store.evolution_jobs(self.scope, self.session.session_id))


def test_asking_about_long_term_rules_is_not_a_save_command():
    from agent_memory.evidence_policy import explicit_persistence_request
    from agent_memory.models import Event, ExtractionResult, ExtractionWindow, Session, Turn
    from agent_memory.providers import _validate_evidence

    question = "我们之前的长期规则是什么？"
    assert not explicit_persistence_request(question)
    assert not explicit_persistence_request("不用保存到长期记忆，这只是临时方案。")
    assert explicit_persistence_request("请把项目默认语言中文存入长期记忆。")
    window = ExtractionWindow(
        session=Session(session_id="s"),
        new_turns=[
            Turn(
                turn_id="t",
                session_id="s",
                sequence=1,
                request_id="r",
                events=[Event(role="user", content=question)],
            )
        ],
        context_turns=[],
    )
    _validate_evidence(window, ExtractionResult(summary="用户询问已有长期规则"))
