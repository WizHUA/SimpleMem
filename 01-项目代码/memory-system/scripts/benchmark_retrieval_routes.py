"""Compare conditional routes with always-on and serialized semantic controls.

Real FTS/BM25, projection reuse, fusion and selection; synthetic embedding latency.
This measures scheduling and call reduction, not semantic quality or production QPS.
"""

import argparse
import asyncio
import json
import statistics
from pathlib import Path
from threading import Event
from time import perf_counter

from agent_memory.models import Evidence, Memory, QueryPlan, Session
from agent_memory.retrieval import Retriever
from agent_memory.settings import Settings


class Planner:
    async def plan(self, query, context):
        return QueryPlan(
            subject="项目",
            predicate="截止日期",
            required_info=["截止日期", "风险"] if "影响" in query else [],
        )


class LatencyEmbedder:
    def __init__(self, delay_ms):
        self.delay_ms = delay_ms
        self.calls = 0

    async def encode(self, texts):
        self.calls += 1
        await asyncio.sleep(self.delay_ms / 1000)
        return [[1.0, 0.0] for _ in texts]


class AlwaysSemantic(Retriever):
    @classmethod
    def _query_type(cls, query, plan):
        return "open"


class SerialSemanticControl(AlwaysSemantic):
    """Ablation: remote I/O starts only after both local routes finish."""

    async def _recall(self, *args):
        self.local_done = [Event(), Event()]
        return await super()._recall(*args)

    def _lexical_recall(self, *args):
        try:
            return super()._lexical_recall(*args)
        finally:
            self.local_done[0].set()

    def _symbolic_recall(self, *args):
        try:
            return super()._symbolic_recall(*args)
        finally:
            self.local_done[1].set()

    async def _semantic_recall(self, *args):
        await asyncio.to_thread(lambda: [event.wait() for event in self.local_done])
        return await super()._semantic_recall(*args)


async def benchmark(size, repeats, delay_ms):
    records = [
        Memory(
            memory_id=f"m{index:05}",
            session_id="origin",
            tier="long",
            scope_type="user",
            scope_id="owner",
            subject="项目",
            predicate="截止日期",
            value="9月20日",
            content=f"项目截止日期为9月20日，记录编号{index}，评审需准备测试日志和风险说明。",
            evidence=[Evidence(turn_id=f"t{index}", event_index=0, quote="9月20日")],
        )
        for index in range(size)
    ]
    report = {"documents": size, "repeats": repeats, "synthetic_embedding_ms": delay_ms, "cases": {}}
    session = Session(session_id="benchmark")
    for query in ("项目截止日期是什么", "项目截止日期有哪些影响"):
        retrievers = {
            name: cls(Settings(), Planner(), LatencyEmbedder(delay_ms))
            for name, cls in (
                ("serial_semantic_control", SerialSemanticControl),
                ("parallel_all", AlwaysSemantic),
                ("conditional", Retriever),
            )
        }
        samples = {name: [] for name in retrievers}
        expected = None
        try:
            for retriever in retrievers.values():
                await retriever.search(query, session, [], records)
                retriever.embedder.calls = 0
            for iteration in range(repeats):
                # Alternate order to reduce systematic warmup/order bias.
                order = list(retrievers.items())
                for name, retriever in order if iteration % 2 == 0 else order[::-1]:
                    started = perf_counter()
                    result = await retriever.search(query, session, [], records)
                    samples[name].append((perf_counter() - started) * 1000)
                    selected = [(hit.chunk_id, hit.score) for hit in result.results]
                    if expected is None:
                        expected = selected
                    assert selected == expected, "control changed selected evidence"
            report["cases"][query] = {
                name: {
                    "median_ms": round(statistics.median(values), 3),
                    "samples_ms": [round(value, 3) for value in values],
                    "embedding_calls": retrievers[name].embedder.calls,
                }
                for name, values in samples.items()
            }
        finally:
            for retriever in retrievers.values():
                retriever.close()
    report["limitations"] = [
        "Synthetic constant embeddings and latency; no external model or network.",
        "Warm single-request searches, in-memory SQLite, authorized snapshot still scanned.",
        "Serial control gates semantic on both local routes, not a historical release benchmark.",
        "Matching selected IDs and scores verifies this fixture only, not general recall quality.",
    ]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=500)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--embedding-ms", type=float, default=40)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.size < 1 or args.repeats < 1 or not 0 <= args.embedding_ms <= 10000:
        parser.error("size/repeats must be positive; embedding-ms must be between 0 and 10000")
    report = asyncio.run(benchmark(args.size, args.repeats, args.embedding_ms))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
