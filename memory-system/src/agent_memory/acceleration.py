"""Bounded exact-context inference reuse; never caches authorization or retrieval.

One event-loop-owned cache per runtime. Failed/cancelled inference is not retained.
Concurrent identical requests share a task; the last departing waiter cancels it.
Only hashes of prompts are retained as keys, and owner deletion clears results.
"""

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .ports import ConflictError


@dataclass
class InferenceResult:
    text: str
    generation_ms: float


@dataclass
class _Flight:
    task: asyncio.Task
    waiters: int = 0
    invalidated: bool = False


class InferenceCache:
    def __init__(self, ttl: float = 60, max_entries: int = 128):
        self.ttl = ttl
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple, tuple[float, InferenceResult]] = OrderedDict()
        self._flights: dict[tuple, _Flight] = {}

    async def run(
        self, key: tuple, generate: Callable[[], Awaitable[str]], *, enabled: bool = True
    ) -> tuple[InferenceResult, str]:
        if not enabled:
            return await self._generate(generate), "disabled"
        now = time.monotonic()
        for old_key, (expires, _) in list(self._entries.items()):
            if expires <= now:
                del self._entries[old_key]
        cached = self._entries.get(key)
        if cached:
            self._entries.move_to_end(key)
            return cached[1], "hit"
        flight = self._flights.get(key)
        status = "shared" if flight else "miss"
        if flight is None:
            # Cap in-flight unique prompts as well as completed entries. Requests
            # beyond the cache capacity still work, without allocating cache state.
            if len(self._flights) >= self.max_entries:
                return await self._generate(generate), "miss"
            flight = _Flight(asyncio.create_task(self._generate(generate)))
            self._flights[key] = flight
        flight.waiters += 1
        try:
            result = await asyncio.shield(flight.task)
            # Do not retain pathological provider responses in memory.
            if self._flights.get(key) is flight and len(result.text) <= 100_000:
                self._entries[key] = (time.monotonic() + self.ttl, result)
                self._entries.move_to_end(key)
                while len(self._entries) > self.max_entries:
                    self._entries.popitem(last=False)
            return result, status
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if flight.invalidated and current is not None and not current.cancelling():
                raise ConflictError("Memory inference invalidated; retry against current state") from None
            raise
        finally:
            flight.waiters -= 1
            if flight.waiters == 0:
                if self._flights.get(key) is flight:
                    del self._flights[key]
                if not flight.task.done():
                    flight.task.cancel()
                # Retrieve late errors/cancellation without abandoning background tasks.
                await asyncio.gather(flight.task, return_exceptions=True)

    @staticmethod
    async def _generate(generate: Callable[[], Awaitable[str]]) -> InferenceResult:
        started = time.perf_counter()
        text = await generate()
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Model answer must be non-empty text")
        return InferenceResult(text, (time.perf_counter() - started) * 1000)

    def clear_scope(self, tenant: str, owner: str) -> None:
        for key in list(self._entries):
            if key[:2] == (tenant, owner):
                del self._entries[key]
        for key, flight in list(self._flights.items()):
            if key[:2] == (tenant, owner):
                del self._flights[key]
                flight.invalidated = True
                flight.task.cancel()

    async def close(self) -> None:
        tasks = [flight.task for flight in self._flights.values()]
        self._flights.clear()
        self._entries.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
