"""Entity presentation keeps atomic evidence independent and respects lifecycle."""

from datetime import timedelta

from agent_memory.models import Evidence, Memory, Scope, utcnow
from agent_memory.store import SQLiteStore


def fact(identifier, **changes):
    fields = {
        "memory_id": identifier,
        "subject": "zfc",
        "predicate": "别名",
        "value": "飞猪",
        "content": "zfc是飞猪",
        "session_id": "s",
        "scope_type": "user",
        "scope_id": "alice",
        "tier": "long",
        "evidence": [Evidence(turn_id="t", event_index=0, quote="zfc是飞猪")],
    }
    fields.update(changes)
    return Memory(**fields)


def test_attributes_combine_read_time_without_mutating_atomic_records(tmp_path):
    store = SQLiteStore(tmp_path / "memory.db")
    scope = Scope(tenant_id="t", owner_id="alice")
    try:
        with store.transaction():
            store._save_memory(scope, fact("name"))
            store._save_memory(
                scope, fact("trait", predicate="特性", value="非常狂暴", content="zfc非常狂暴")
            )
        before = [m.model_dump() for m in store.memories(scope)]
        entities = store.entities(scope)
        assert len(entities) == 1
        assert len(entities[0].facts) == 2
        assert "别名：飞猪" in entities[0].summary
        assert "特性：非常狂暴" in entities[0].summary
        assert entities[0].conflict_predicates == []
        assert [m.model_dump() for m in store.memories(scope)] == before
        assert store.entities(scope)[0].entity_id == entities[0].entity_id
    finally:
        store.close()


def test_conflicts_pending_and_scope_boundaries_are_not_flattened(tmp_path):
    store = SQLiteStore(tmp_path / "memory.db")
    scope = Scope(tenant_id="t", owner_id="alice")
    other = Scope(tenant_id="t", owner_id="bob")
    try:
        with store.transaction():
            store._save_memory(scope, fact("name"))
            store._save_memory(scope, fact("pending", status="pending", value="飞猫"))
            store._save_memory(scope, fact("project", scope_type="project", scope_id="p"))
            store._save_memory(other, fact("secret", value="私人值"))
        entities = store.entities(scope)
        assert len(entities) == 2
        user = next(item for item in entities if item.scope_type == "user")
        assert user.summary == "别名：待核对"
        assert len(user.facts) == len(user.pending_facts) == 1
        assert user.conflict_predicates == ["别名"]
        assert "私人值" not in str(entities)
    finally:
        store.close()


def test_expired_future_retracted_archived_and_superseded_facts_excluded(tmp_path):
    store = SQLiteStore(tmp_path / "memory.db")
    scope = Scope(tenant_id="t", owner_id="alice")
    now = utcnow()
    try:
        with store.transaction():
            for status in ("retracted", "archived", "superseded"):
                store._save_memory(scope, fact(status, status=status))
            store._save_memory(scope, fact("expired", valid_to=now - timedelta(seconds=1)))
            store._save_memory(scope, fact("future", valid_from=now + timedelta(days=1)))
            store._save_memory(scope, fact("short", tier="short"))
            store._save_memory(
                scope, fact("scheduled-old", status="superseded", valid_to=now + timedelta(days=1))
            )
        entities = store.entities(scope)
        assert [m.memory_id for m in entities[0].facts] == ["scheduled-old"]
    finally:
        store.close()
