"""Periodic worker performs real lifecycle work and shuts down cooperatively."""

import asyncio
from datetime import timedelta

import pytest

from agent_memory.models import Candidate, Event, Evidence, Scope, TurnInput, utcnow
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


def test_periodic_worker_archives_expired_event_and_stops(tmp_path):
    async def run():
        runtime = MemoryRuntime(Settings(db_path=tmp_path / "worker.db", maintenance_interval=0.01))
        scope = Scope(tenant_id="t", owner_id="a")
        session = runtime.store.create_session(scope)
        turn = runtime.store.append_turn(
            scope,
            session.session_id,
            TurnInput(request_id="r", events=[Event(role="user", content="会议已经结束。")]),
        )
        memory = runtime.store.add_candidates(
            scope,
            session.session_id,
            [
                Candidate(
                    subject="会议",
                    predicate="状态",
                    value="结束",
                    content="会议已经结束。",
                    kind="event",
                    valid_to=utcnow() - timedelta(seconds=1),
                    evidence=[Evidence(turn_id=turn.turn_id, event_index=0, quote="会议已经结束。")],
                )
            ],
        )[0]
        completed = asyncio.Event()
        original = runtime.maintain

        async def maintain(who):
            result = await original(who)
            completed.set()
            return result

        runtime.maintain = maintain
        task = asyncio.create_task(runtime.maintenance_worker(scope))
        try:
            await asyncio.wait_for(completed.wait(), 3)
            assert runtime.store.get_memory(scope, memory.memory_id).status == "archived"
            assert runtime.maintenance_state["last_completed_at"]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.close()
        assert task.cancelled()

    asyncio.run(run())


def test_worker_interval_can_be_disabled_but_not_negative():
    assert Settings(maintenance_interval=0).maintenance_interval == 0
    with pytest.raises(ValueError):
        Settings(maintenance_interval=-1)
