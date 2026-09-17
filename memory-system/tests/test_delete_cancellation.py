"""Owner deletion must finish cleaning derived copies after request cancellation."""

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path

from agent_memory.api import create_app
from agent_memory.models import Event, Scope, TurnInput
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


class DeletionCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_api_delete_waits_for_canonical_and_projection_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = MemoryRuntime(Settings(db_path=Path(folder) / "memory.sqlite3"))
            scope = Scope(tenant_id="t", owner_id="o")
            session = runtime.store.create_session(scope)
            await runtime.append_turn(
                scope,
                session.session_id,
                TurnInput(request_id="data", events=[Event(role="user", content="项目暗号是海棠红。")]),
            )
            await runtime.search(scope, session.session_id, "暗号")
            app = create_app(runtime=runtime)
            endpoint = next(
                route.endpoint for route in app.routes if getattr(route, "path", None) == "/api/v1/owner/data"
            )
            entered, release = threading.Event(), threading.Event()
            original = runtime.store.delete_owner

            def delayed_delete(owner):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("test did not release deletion")
                return original(owner)

            runtime.store.delete_owner = delayed_delete
            task = asyncio.create_task(endpoint(scope=scope, service=runtime))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()  # Repeated cancellation must not propagate to the cleanup operation.
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(runtime.store.sessions(scope), [])
                rows = runtime.retriever.projection._connection.execute(
                    "SELECT COUNT(*) FROM projection_docs"
                ).fetchone()[0]
                self.assertEqual(rows, 0)
                self.assertFalse(runtime._deletion_tasks)
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                await runtime.close()

    async def test_runtime_close_drains_pending_deletion_before_closing_storage(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = MemoryRuntime(Settings(db_path=Path(folder) / "memory.sqlite3"))
            scope = Scope(tenant_id="t", owner_id="o")
            runtime.store.create_session(scope)
            entered, release = threading.Event(), threading.Event()
            original = runtime.store.delete_owner

            def delayed_delete(owner):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("test did not release deletion")
                return original(owner)

            runtime.store.delete_owner = delayed_delete
            task = asyncio.create_task(runtime.delete_owner(scope))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            close = asyncio.create_task(runtime.close())
            try:
                await asyncio.sleep(0)
                self.assertFalse(close.done())
                release.set()
                deleted = await task
                self.assertEqual(deleted["sessions"], 1)
                await close
            finally:
                release.set()
                await asyncio.gather(task, close, return_exceptions=True)
