"""Exercise a real ASGI disconnect rather than consuming an entire buffered stream."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from agent_memory.api import create_app
from agent_memory.models import QueryPlan, Scope
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


class StreamDisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_cancels_generation_and_releases_inflight_cache(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        class Model:
            async def plan(self, query, context):
                return QueryPlan()

            async def answer(self, prompt):
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()

        with tempfile.TemporaryDirectory() as folder:
            runtime = MemoryRuntime(Settings(db_path=Path(folder) / "memory.sqlite3"), model=Model())
            session = runtime.store.create_session(Scope(tenant_id="local", owner_id="demo"))
            app = create_app(runtime=runtime)
            body = json.dumps({"session_id": session.session_id, "query": "你好"}).encode()
            delivered = False
            messages = []

            async def receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                await started.wait()
                return {"type": "http.disconnect"}

            async def send(message):
                messages.append(message)

            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": f"/api/v1/sessions/{session.session_id}/answer/stream",
                "raw_path": b"/",
                "query_string": b"",
                "headers": [(b"content-type", b"application/json")],
                "client": ("127.0.0.1", 1),
                "server": ("test", 80),
                "root_path": "",
            }
            try:
                async with app.router.lifespan_context(app):
                    await asyncio.wait_for(app(scope, receive, send), timeout=3)
                    await asyncio.wait_for(cancelled.wait(), timeout=1)
                    await asyncio.sleep(0)
                    self.assertFalse(runtime.inference_cache._flights)
                    self.assertFalse(runtime.inference_cache._entries)
                    payload = b"".join(message.get("body", b"") for message in messages)
                    self.assertNotIn(b'"type": "result"', payload)
            finally:
                await runtime.close()
