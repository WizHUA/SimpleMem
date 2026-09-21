"""Inference reuse correctness, cancellation and memory-change regression tests."""

import asyncio
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from agent_memory.acceleration import InferenceCache
from agent_memory.models import Candidate, Event, Evidence, QueryPlan, Scope, TurnInput, utcnow
from agent_memory.ports import ConflictError, NotFoundError
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_hits_bypass_ttl_lru_and_scope_clear(self):
        cache = InferenceCache(ttl=0.02, max_entries=2)
        calls = 0

        async def generate():
            nonlocal calls
            calls += 1
            return str(calls)

        a, b, c = [("tenant", "owner", value) for value in ("a", "b", "c")]
        self.assertEqual((await cache.run(a, generate))[1], "miss")
        self.assertEqual((await cache.run(a, generate))[1], "hit")
        self.assertEqual((await cache.run(a, generate, enabled=False))[1], "disabled")
        await cache.run(b, generate)
        await cache.run(c, generate)
        self.assertEqual((await cache.run(a, generate))[1], "miss")
        await asyncio.sleep(0.03)
        self.assertEqual((await cache.run(a, generate))[1], "miss")
        cache.clear_scope("tenant", "other")
        self.assertEqual((await cache.run(a, generate))[1], "hit")
        cache.clear_scope("tenant", "owner")
        self.assertEqual((await cache.run(a, generate))[1], "miss")
        await cache.close()

    async def test_singleflight_waiter_cancellation_does_not_cancel_other_waiters(self):
        cache = InferenceCache()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def generate():
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return "one inference"

        first = asyncio.create_task(cache.run(("t", "a", "q"), generate))
        await entered.wait()
        second = asyncio.create_task(cache.run(("t", "a", "q"), generate))
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        result, status = await second
        self.assertEqual((result.text, status, calls), ("one inference", "shared", 1))
        await cache.close()

    async def test_last_waiter_timeout_cancels_inference_and_failure_is_not_cached(self):
        cache = InferenceCache()
        cancelled = asyncio.Event()

        async def stuck():
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        with self.assertRaises(TimeoutError):
            async with asyncio.timeout(0.01):
                await cache.run(("t", "a"), stuck)
        self.assertTrue(cancelled.is_set())
        self.assertFalse(cache._flights)

        async def broken():
            raise RuntimeError("provider failure")

        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "provider failure"):
                await cache.run(("t", "a"), broken)
        self.assertFalse(cache._entries)

    async def test_clear_scope_cancels_inflight_and_cannot_repopulate(self):
        cache = InferenceCache()
        entered = asyncio.Event()

        async def generate():
            entered.set()
            await asyncio.Future()

        task = asyncio.create_task(cache.run(("t", "a", "q"), generate))
        await entered.wait()
        cache.clear_scope("t", "a")
        with self.assertRaises(ConflictError):
            await task
        self.assertFalse(cache._entries)
        self.assertFalse(cache._flights)


class CountingModel:
    def __init__(self):
        self.answers = 0
        self.plans = 0

    async def plan(self, query, context):
        self.plans += 1
        return QueryPlan()

    async def answer(self, prompt):
        self.answers += 1
        return "依据当前证据回答【来源1】"


class RuntimeAccelerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.model = CountingModel()
        self.runtime = MemoryRuntime(Settings(db_path=Path(self.temp.name) / "db"), model=self.model)
        self.scope = Scope(tenant_id="t", owner_id="a")
        self.session = self.runtime.store.create_session(self.scope, "项目", "p")
        await self.append("one", "项目使用中文")

    async def asyncTearDown(self):
        await self.runtime.close()
        self.temp.cleanup()

    async def append(self, request, text):
        await self.runtime.append_turn(
            self.scope,
            self.session.session_id,
            TurnInput(request_id=request, events=[Event(role="user", content=text)]),
        )

    async def answer(self, **kwargs):
        return await self.runtime.answer(self.scope, self.session.session_id, "项目语言", **kwargs)

    async def test_reuses_only_generation_after_fresh_authorized_retrieval(self):
        first, second = await self.answer(), await self.answer()
        self.assertEqual(first.generated_text, second.generated_text)
        self.assertEqual(second.acceleration.cache_status, "hit")
        self.assertEqual(second.acceleration.avoided_model_calls, 1)
        self.assertEqual((self.model.answers, self.model.plans), (1, 2))
        self.assertGreater(second.acceleration.context_tokens_after, 0)
        self.assertGreaterEqual(
            second.acceleration.context_tokens_before, second.acceleration.context_tokens_after
        )
        baseline = await self.answer(accelerate=False)
        self.assertEqual(baseline.acceleration.cache_status, "disabled")
        self.assertEqual(self.model.answers, 2)
        with self.assertRaises(NotFoundError):
            await self.runtime.answer(Scope(tenant_id="t", owner_id="b"), self.session.session_id, "项目语言")

    async def test_new_turn_state_and_model_change_invalidate(self):
        await self.answer()
        await self.append("two", "项目改用英文")
        self.assertEqual((await self.answer()).acceleration.cache_status, "miss")
        self.runtime.clear_scope_cache(self.scope)
        self.assertEqual((await self.answer()).acceleration.cache_status, "miss")
        self.runtime.model = CountingModel()
        self.assertEqual((await self.answer()).acceleration.cache_status, "miss")

    async def test_zero_context_never_exposes_all_raw_history(self):
        from dataclasses import replace

        self.runtime.settings = replace(self.runtime.settings, context_turns=0)
        result = await self.runtime.search(self.scope, self.session.session_id, "项目")
        self.assertEqual(result.results, [])

    async def test_large_legal_raw_event_is_chunked_without_losing_tail(self):
        await self.append("large", "填充" * 1100 + "唯一标识 TailNeedle")
        result = await self.runtime.search(self.scope, self.session.session_id, "TailNeedle")
        self.assertTrue(result.results)
        self.assertIn("TailNeedle", result.results[0].content)
        self.assertIn(
            result.results[0].metadata["evidence"][0]["quote"], "填充" * 1100 + "唯一标识 TailNeedle"
        )

    async def test_changed_snapshot_never_publishes_generated_result(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_answer(prompt):
            entered.set()
            await release.wait()
            return "outdated answer"

        self.model.answer = slow_answer
        request = asyncio.create_task(self.answer())
        await entered.wait()
        await self.append("mutation", "项目改成日语")
        release.set()
        with self.assertRaises(ConflictError):
            await request
        self.assertFalse(self.runtime.inference_cache._entries)

    async def test_deleted_owner_never_publishes_uncached_inference(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_answer(prompt):
            entered.set()
            await release.wait()
            return "deleted data"

        self.model.answer = slow_answer
        request = asyncio.create_task(self.answer(accelerate=False))
        await entered.wait()
        self.runtime.store.delete_owner(self.scope)
        self.runtime.clear_scope_cache(self.scope)
        release.set()
        with self.assertRaises(NotFoundError):
            await request

    async def test_deleted_owner_never_publishes_inflight_search(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_plan(query, context):
            entered.set()
            await release.wait()
            return QueryPlan()

        self.model.plan = slow_plan
        request = asyncio.create_task(self.runtime.search(self.scope, self.session.session_id, "项目"))
        await entered.wait()
        self.runtime.store.delete_owner(self.scope)
        self.runtime.clear_scope_cache(self.scope)
        release.set()
        with self.assertRaises(NotFoundError):
            await request

    async def test_time_boundary_during_planning_requires_fresh_snapshot(self):
        clock = [utcnow()]
        turn = self.runtime.store.turns(self.scope, self.session.session_id)[0]
        self.runtime.store.add_candidates(
            self.scope,
            self.session.session_id,
            [
                Candidate(
                    content="项目使用中文",
                    subject="项目",
                    predicate="语言",
                    value="中文",
                    valid_to=clock[0] + timedelta(seconds=1),
                    evidence=[Evidence(turn_id=turn.turn_id, event_index=0, quote="项目使用中文")],
                )
            ],
        )

        async def crosses_boundary(query, context):
            clock[0] += timedelta(seconds=2)
            return QueryPlan()

        self.model.plan = crosses_boundary
        with (
            patch("agent_memory.runtime.utcnow", side_effect=lambda: clock[0]),
            patch("agent_memory.retrieval.utcnow", side_effect=lambda: clock[0]),
            self.assertRaisesRegex(ConflictError, "validity changed"),
        ):
            await self.runtime.search(self.scope, self.session.session_id, "项目语言")

    async def test_time_boundary_during_generation_rejects_obsolete_answer(self):
        clock = [utcnow()]
        turn = self.runtime.store.turns(self.scope, self.session.session_id)[0]
        self.runtime.store.add_candidates(
            self.scope,
            self.session.session_id,
            [
                Candidate(
                    content="项目使用中文",
                    subject="项目",
                    predicate="语言",
                    value="中文",
                    valid_to=clock[0] + timedelta(seconds=1),
                    evidence=[Evidence(turn_id=turn.turn_id, event_index=0, quote="项目使用中文")],
                )
            ],
        )

        async def crosses_boundary(prompt):
            clock[0] += timedelta(seconds=2)
            return "obsolete"

        self.model.answer = crosses_boundary
        with (
            patch("agent_memory.runtime.utcnow", side_effect=lambda: clock[0]),
            patch("agent_memory.retrieval.utcnow", side_effect=lambda: clock[0]),
            self.assertRaisesRegex(ConflictError, "validity changed"),
        ):
            await self.answer()
        self.assertFalse(self.runtime.inference_cache._entries)

    async def test_equivalent_prompt_reuses_generation_despite_score_changes(self):
        count = 0

        async def variable_plan(query, context):
            nonlocal count
            count += 1
            return QueryPlan(keywords=["中文"] if count % 2 else [])

        self.model.plan = variable_plan
        await self.answer()
        result = await self.answer()
        self.assertTrue(result.acceleration.cache_hit)
        self.assertEqual(self.model.answers, 1)


if __name__ == "__main__":
    unittest.main()
