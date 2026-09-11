"""Stateful checks for the small local backend; no model or external service."""

import asyncio
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from agent_memory.models import Candidate, Event, Evidence, EvolutionInput, Scope, TurnInput, utcnow
from agent_memory.ports import ConflictError, ModelNotConfigured, NotFoundError
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        settings = Settings(db_path=Path(self.folder.name) / "memory.db", retrieval_token_limit=20000)
        self.runtime = MemoryRuntime(settings)
        self.alice = Scope(tenant_id="tenant", owner_id="alice")
        self.bob = Scope(tenant_id="tenant", owner_id="bob")
        self.session = await asyncio.to_thread(
            self.runtime.store.create_session, self.alice, "项目 P 验收", "project-p"
        )

    async def asyncTearDown(self):
        await self.runtime.close()
        self.folder.cleanup()

    async def append(self, request_id="request-1", content="这次评审材料使用英文。"):
        result = await self.runtime.append_turn(
            self.alice,
            self.session.session_id,
            TurnInput(request_id=request_id, events=[Event(role="user", content=content)]),
        )
        return result["turn"]

    async def test_recent_raw_message_is_searchable_before_extraction(self):
        await self.append()
        result = await self.runtime.search(self.alice, self.session.session_id, "这次评审材料使用什么语言")
        self.assertTrue(result.results)
        self.assertEqual(result.results[0].metadata["tier"], "short")
        self.assertTrue(result.results[0].metadata["memory_id"].startswith("raw_"))
        self.assertLessEqual(result.selected_k, 10)

    async def test_idempotent_turn_and_owner_isolation(self):
        first = await self.append()
        second = await self.append()
        self.assertEqual(first.turn_id, second.turn_id)
        with self.assertRaises(ConflictError):
            await self.runtime.append_turn(
                self.alice,
                self.session.session_id,
                TurnInput(request_id="request-1", events=[Event(role="user", content="另一条消息")]),
            )
        with self.assertRaises(NotFoundError):
            await self.runtime.search(self.bob, self.session.session_id, "评审材料")

    async def test_unconfigured_extraction_preserves_pending_turn(self):
        turn = await self.append()
        with self.assertRaises(ModelNotConfigured):
            await self.runtime.short_term.extract(self.alice, self.session.session_id)
        session = await asyncio.to_thread(self.runtime.store.get_session, self.alice, self.session.session_id)
        self.assertEqual(session.processed_sequence, 0)
        self.assertEqual(
            (await self.runtime.short_term.window(self.alice, self.session.session_id)).new_turns[0].turn_id,
            turn.turn_id,
        )

    async def test_extraction_due_after_five_pending_turns(self):
        last = None
        for index in range(5):
            last = await self.runtime.append_turn(
                self.alice,
                self.session.session_id,
                TurnInput(
                    request_id=f"pending-{index}",
                    events=[Event(role="user", content=f"第 {index + 1} 条待处理消息")],
                ),
            )
        self.assertTrue(last["extraction_due"])
        self.assertIn("pending_turn_limit", last["extraction_reasons"])
        self.assertEqual(last["pending_turns"], 5)

    async def test_promote_then_supersede_preserves_history_and_current_value(self):
        now = utcnow()
        old_turn = await self.append("old", "项目 P 当前验收截止日期是 2026 年 9 月 10 日。")
        old = Candidate(
            content="项目 P 的验收截止日期是 2026-09-10。",
            subject="项目 P",
            predicate="验收截止日期",
            value="2026-09-10",
            scope_type="project",
            durable=True,
            valid_from=now - timedelta(days=2),
            evidence=[
                Evidence(turn_id=old_turn.turn_id, event_index=0, quote="验收截止日期是 2026 年 9 月 10 日")
            ],
            hierarchy_path=["项目", "项目 P", "验收"],
        )
        old_memory = (
            await asyncio.to_thread(
                self.runtime.store.add_candidates, self.alice, self.session.session_id, [old]
            )
        )[0]
        long_old = await self.runtime.long_term.evolve(
            self.alice,
            old_memory.memory_id,
            EvolutionInput(action="promote", expected_version=1),
        )

        new_turn = await self.append("new", "项目 P 的验收截止日期调整为 2026 年 9 月 12 日。")
        new = old.model_copy(
            update={
                "content": "项目 P 的验收截止日期调整为 2026-09-12。",
                "value": "2026-09-12",
                "valid_from": None,
                "evidence": [
                    Evidence(
                        turn_id=new_turn.turn_id, event_index=0, quote="验收截止日期调整为 2026 年 9 月 12 日"
                    )
                ],
            }
        )
        new_memory = (
            await asyncio.to_thread(
                self.runtime.store.add_candidates, self.alice, self.session.session_id, [new]
            )
        )[0]
        effective = now - timedelta(hours=1)
        current = await self.runtime.long_term.evolve(
            self.alice,
            new_memory.memory_id,
            EvolutionInput(
                action="supersede",
                expected_version=1,
                target_id=long_old.memory_id,
                target_version=1,
                effective_at=effective,
            ),
        )
        self.assertEqual((current.version, current.value, current.status), (2, "2026-09-12", "active"))
        versions = [
            m
            for m in await asyncio.to_thread(self.runtime.store.memories, self.alice)
            if m.memory_id == current.memory_id
        ]
        self.assertEqual({(m.version, m.status) for m in versions}, {(1, "superseded"), (2, "active")})
        audit = await asyncio.to_thread(self.runtime.store.history, self.alice, current.memory_id)
        self.assertTrue(any(row["action"] == "close_validity" for row in audit))

        current_search = await self.runtime.search(
            self.alice, self.session.session_id, "项目 P 现在的验收截止日期"
        )
        self.assertTrue(any(h.metadata["version"] == 2 for h in current_search.results))
        old_search = await self.runtime.search(
            self.alice, self.session.session_id, "项目 P 之前的验收截止日期"
        )
        self.assertTrue(any(h.metadata["version"] == 1 for h in old_search.results))

    async def test_hmem_group_is_disposable_reference_view(self):
        turn = await self.append("durable", "用户以后默认使用中文。")
        candidate = Candidate(
            content="用户默认使用中文。",
            kind="preference",
            subject="用户",
            predicate="默认语言",
            value="中文",
            scope_type="user",
            durable=True,
            evidence=[Evidence(turn_id=turn.turn_id, event_index=0, quote="以后默认使用中文")],
            hierarchy_path=["个人", "偏好", "语言"],
        )
        memory = (
            await asyncio.to_thread(
                self.runtime.store.add_candidates, self.alice, self.session.session_id, [candidate]
            )
        )[0]
        await self.runtime.long_term.evolve(
            self.alice, memory.memory_id, EvolutionInput(action="promote", expected_version=1)
        )
        report = await self.runtime.long_term.maintain(self.alice)
        self.assertEqual(report["mode"], "reference_group")
        self.assertEqual(report["semantic_synthesis"], "not_implemented")
        self.assertEqual(report["groups"][0]["path"], ["个人", "偏好", "语言"])


if __name__ == "__main__":
    unittest.main()
