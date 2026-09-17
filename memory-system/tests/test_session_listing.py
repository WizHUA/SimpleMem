"""Session navigation is authenticated, owner scoped and server-ordered."""

import asyncio

from fastapi.testclient import TestClient

from agent_memory.api import create_app
from agent_memory.models import Event, Scope, TurnInput
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


def test_session_listing_auth_scope_and_recent_update_order(tmp_path):
    settings = Settings(db_path=tmp_path / "memory.db", api_key="a" * 32)
    runtime = MemoryRuntime(settings)
    alice = Scope(tenant_id=settings.local_tenant, owner_id=settings.local_owner)
    bob = Scope(tenant_id=settings.local_tenant, owner_id="another-owner")
    first = runtime.store.create_session(alice, "早会话")
    second = runtime.store.create_session(alice, "新会话")
    runtime.store.create_session(bob, "不能泄漏")
    try:
        with TestClient(create_app(runtime=runtime)) as client:
            assert client.get("/api/v1/sessions").status_code == 401
            headers = {"Authorization": "Bearer " + settings.api_key}
            data = client.get("/api/v1/sessions", headers=headers).json()
            assert [item["session_id"] for item in data] == [second.session_id, first.session_id]
            runtime.store.append_turn(
                alice,
                first.session_id,
                TurnInput(request_id="update", events=[Event(role="user", content="继续")]),
            )
            data = client.get("/api/v1/sessions?owner_id=another-owner", headers=headers).json()
            assert [item["session_id"] for item in data] == [first.session_id, second.session_id]
            assert data[0]["updated_at"] >= data[0]["created_at"]
            assert "不能泄漏" not in str(data)
    finally:
        asyncio.run(runtime.close())
