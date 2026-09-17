"""Answer provenance is bound to an exact server answer, never supplied by clients."""

from datetime import timedelta

import pytest

from agent_memory.models import AnswerContext, AnswerResponse, Event, Hit, Scope, TurnInput, utcnow
from agent_memory.ports import ConflictError, NotFoundError
from agent_memory.store import SQLiteStore


@pytest.fixture
def setup(tmp_path):
    store = SQLiteStore(tmp_path / "memory.sqlite3")
    scope = Scope(tenant_id="tenant", owner_id="alice")
    session = store.create_session(scope).session_id
    yield store, scope, session
    store.close()


def response(text="是的，zfc 就是飞猪。【来源1】"):
    return AnswerResponse(
        generated_text=text,
        citations=[1],
        retrieval_count=1,
        elapsed_ms=12,
        sources=[
            Hit(
                content="zfc 就是飞猪",
                score=1,
                source_file="memory:m1",
                chunk_id="m1:1",
                metadata={"memory_id": "m1", "version": 1, "status": "active"},
            )
        ],
    )


def append(store, scope, session, answer_id, content, request="r1", **extra):
    return store.append_turn(
        scope,
        session,
        TurnInput(
            request_id=request,
            events=[Event(role="assistant", content=content, answer_id=answer_id, **extra)],
        ),
    )


def test_snapshot_survives_refresh_and_excludes_prompt(setup):
    store, scope, session = setup
    answer = response()
    events = [{"phase": "retrieval", "status": "completed"}]
    receipt = store.save_answer(scope, session, answer, events)
    answer.sources[0].metadata["status"] = "superseded"
    events[0]["status"] = "changed"
    turn = append(store, scope, session, receipt, answer.generated_text)
    restored = store.turns(scope, session)[0]
    context = restored.events[0].answer_context
    assert context.sources[0].metadata["status"] == "active"
    assert context.run_events[0]["status"] == "completed"
    assert restored == turn
    assert "answer_context" not in str(restored.prompt_dump())
    assert "answer_id" not in str(restored.prompt_dump())
    assert restored.prompt_dump()["events"][0]["content"] == answer.generated_text
    assert append(store, scope, session, receipt, answer.generated_text).turn_id == turn.turn_id
    with pytest.raises(ConflictError):
        append(store, scope, session, receipt, answer.generated_text, request="another")


def test_forged_context_and_mismatched_body_rejected_without_consuming_receipt(setup):
    store, scope, session = setup
    answer = response()
    receipt = store.save_answer(scope, session, answer, [])
    with pytest.raises(ValueError, match="server-managed"):
        append(store, scope, session, receipt, answer.generated_text, answer_context=AnswerContext())
    with pytest.raises(ValueError, match="does not match"):
        append(store, scope, session, receipt, "伪造正文")
    with pytest.raises(ValueError, match="Only assistant"):
        store.append_turn(
            scope,
            session,
            TurnInput(
                request_id="user",
                events=[Event(role="user", content=answer.generated_text, answer_id=receipt)],
            ),
        )
    assert append(store, scope, session, receipt, answer.generated_text).events[0].answer_context


def test_scope_session_and_receipt_swap_isolation(setup):
    store, scope, session = setup
    answer = response()
    receipt = store.save_answer(scope, session, answer, [])
    other_session = store.create_session(scope).session_id
    other_owner = Scope(tenant_id="tenant", owner_id="bob")
    other_owner_session = store.create_session(other_owner).session_id
    for principal, sid in [(scope, other_session), (other_owner, other_owner_session)]:
        with pytest.raises(ConflictError, match="receipt unavailable"):
            append(store, principal, sid, receipt, answer.generated_text)
    second = response("另一条回答。【来源1】")
    second_id = store.save_answer(scope, session, second, [])
    with pytest.raises(ValueError):
        append(store, scope, session, second_id, answer.generated_text)
    assert append(store, scope, session, receipt, answer.generated_text).events[0].answer_id == receipt


def test_legacy_turn_has_no_inferred_sources(setup):
    store, scope, session = setup
    answer = response()
    store.save_answer(scope, session, answer, [])
    turn = append(store, scope, session, None, answer.generated_text)
    assert turn.events[0].answer_context is None


def test_bounded_pending_receipts_and_owner_delete(setup):
    store, scope, session = setup
    answer = response()
    old_id = store.save_answer(scope, session, answer, [])
    for _ in range(200):
        latest = store.save_answer(scope, session, answer, [])
    with pytest.raises(ConflictError, match="expired"):
        append(store, scope, session, old_id, answer.generated_text)
    append(store, scope, session, latest, answer.generated_text)
    deleted = store.delete_owner(scope)
    assert deleted["answers"] == 199
    assert deleted["turns"] == 1


def test_append_transaction_rolls_back_receipt_consumption(setup):
    store, scope, session = setup
    answer = response()
    receipt = store.save_answer(scope, session, answer, [])
    with pytest.raises(ConflictError):
        store.append_turn(
            scope,
            session,
            TurnInput(
                request_id="batch",
                events=[
                    Event(role="assistant", content=answer.generated_text, answer_id=receipt),
                    Event(role="assistant", content="bad", answer_id="not-a-receipt"),
                ],
            ),
        )
    assert store.turns(scope, session) == []
    assert append(store, scope, session, receipt, answer.generated_text).events[0].answer_context


def test_receipt_version_fence_and_deleted_session(setup):
    store, scope, session = setup
    version = store.generation_version(scope, session)
    answer = response()
    store.save_answer(scope, session, answer, [], expected_version=version)
    store.append_turn(
        scope, session, TurnInput(request_id="new", events=[Event(role="user", content="改了")])
    )
    with pytest.raises(ConflictError, match="Memory changed"):
        store.save_answer(scope, session, answer, [], expected_version=version)
    assert store._db.execute("SELECT COUNT(*) FROM answers").fetchone()[0] == 1
    store.delete_owner(scope)
    with pytest.raises(NotFoundError):
        store.save_answer(scope, session, answer, [], expected_version=version)
    assert store._db.execute("SELECT COUNT(*) FROM answers").fetchone()[0] == 0


def test_receipt_validity_fence_checks_boundary_inside_transaction(setup, monkeypatch):
    store, scope, session = setup
    boundary = utcnow()
    monkeypatch.setattr("agent_memory.store.utcnow", lambda: boundary)
    with pytest.raises(ConflictError, match="validity changed"):
        store.save_answer(scope, session, response(), [], expected_valid_until=boundary)
    store.save_answer(scope, session, response(), [], expected_valid_until=boundary + timedelta(seconds=1))
    assert store._db.execute("SELECT COUNT(*) FROM answers").fetchone()[0] == 1
