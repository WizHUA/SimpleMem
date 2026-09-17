"""Deletion must fence snapshots captured before their projection begins syncing."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from agent_memory.models import Event, Scope, TurnInput
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


class DeleteRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_then_cancel_cannot_repopulate_projection_from_old_snapshot(self):
        snapshot_ready, continue_search, planner_entered = (asyncio.Event() for _ in range(3))

        class Model:
            async def plan(self, query, context):
                planner_entered.set()
                await asyncio.Future()

        with tempfile.TemporaryDirectory() as folder:
            runtime = MemoryRuntime(Settings(db_path=Path(folder) / "memory.sqlite3"), model=Model())
            scope = Scope(tenant_id="t", owner_id="o")
            session = runtime.store.create_session(scope)
            await runtime.append_turn(
                scope,
                session.session_id,
                TurnInput(request_id="secret", events=[Event(role="user", content="我的秘密暗号是海棠红。")]),
            )
            original = runtime.retriever.search

            async def delayed_search(*args, **kwargs):
                snapshot_ready.set()
                await continue_search.wait()
                return await original(*args, **kwargs)

            runtime.retriever.search = delayed_search
            task = asyncio.create_task(runtime.search(scope, session.session_id, "暗号是什么"))
            try:
                await snapshot_ready.wait()
                await asyncio.to_thread(runtime.store.delete_owner, scope)
                runtime.clear_scope_cache(scope)
                continue_search.set()
                planner_wait = asyncio.create_task(planner_entered.wait())
                done, _ = await asyncio.wait(
                    {task, planner_wait}, timeout=2, return_when=asyncio.FIRST_COMPLETED
                )
                self.assertTrue(done)
                if planner_wait in done:
                    task.cancel()
                planner_wait.cancel()
                await asyncio.gather(planner_wait, return_exceptions=True)
                await asyncio.gather(task, return_exceptions=True)
                rows = runtime.retriever.projection._connection.execute(
                    "SELECT COUNT(*) FROM projection_docs"
                ).fetchone()[0]
                self.assertEqual(rows, 0, "A deleted owner's cancelled request recreated its FTS document")
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await runtime.close()
