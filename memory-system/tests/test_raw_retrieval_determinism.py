"""Stable raw event timestamps keep source ordering and exact generation reuse stable."""

import asyncio
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from agent_memory.models import Event, QueryPlan, Scope, TurnInput, utcnow
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


class RawDeterminismTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_and_concurrent_answers_reuse_identical_raw_context(self):
        class Model:
            def __init__(self):
                self.calls = 0

            async def plan(self, query, context):
                return QueryPlan()

            async def answer(self, prompt):
                self.calls += 1
                await asyncio.sleep(0.025)
                return "项目语言是中文【来源1】"

        with tempfile.TemporaryDirectory() as folder:
            model = Model()
            runtime = MemoryRuntime(Settings(db_path=Path(folder) / "memory.sqlite3"), model=model)
            scope = Scope(tenant_id="t", owner_id="o")
            session = runtime.store.create_session(scope)
            occurred = utcnow() - timedelta(days=1)
            for index in range(20):
                await runtime.append_turn(
                    scope,
                    session.session_id,
                    TurnInput(
                        request_id=str(index),
                        events=[
                            Event(
                                role="user",
                                content="项目语言是中文。",
                                occurred_at=occurred + timedelta(milliseconds=index),
                            )
                        ],
                    ),
                )
            original = runtime.retriever.search
            captured = []

            async def capture(query, state, short, long, **kwargs):
                captured.extend(
                    (memory.recorded_at, memory.valid_from)
                    for memory in short
                    if memory.memory_id.startswith("raw_")
                )
                return await original(query, state, short, long, **kwargs)

            runtime.retriever.search = capture
            try:
                responses = [await runtime.answer(scope, session.session_id, "项目语言") for _ in range(10)]
                self.assertEqual(model.calls, 1)
                self.assertTrue(all(recorded == event_time for recorded, event_time in captured))
                self.assertTrue(
                    all(response.acceleration.cache_status == "hit" for response in responses[1:])
                )
                runtime.clear_scope_cache(scope)
                model.calls = 0
                await asyncio.gather(
                    *[runtime.answer(scope, session.session_id, "项目语言") for _ in range(8)]
                )
                self.assertEqual(model.calls, 1)
            finally:
                await runtime.close()
