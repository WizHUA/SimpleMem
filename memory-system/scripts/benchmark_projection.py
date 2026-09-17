"""Reproducible full-scan versus FTS projection benchmark, without network/model time."""

import argparse
import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path

from agent_memory.models import Evidence, Memory, Session
from agent_memory.retrieval import Retriever
from agent_memory.retrieval_projection import RetrievalProjection
from agent_memory.settings import Settings


class FullScanProjection(RetrievalProjection):
    """Control: same retriever, but every eligible document reaches Python BM25."""

    def sync(self, namespace, memories, tokenize, generation):
        return {"documents": len(memories), "changed": 0, "removed": 0}

    def candidates(self, namespace, terms, eligible, limit, generation):
        return [self.key(memory) for memory in eligible]


class CountingEmbedder:
    endpoint = "https://synthetic.invalid/embeddings"
    model = "deterministic-benchmark-v1"

    def __init__(self):
        self.input_count = 0

    async def encode(self, texts):
        self.input_count += len(texts)
        return [[1.0, 0.0] for _ in texts]


async def benchmark(size, repeats):
    memories = []
    for index in range(size):
        content = f"项目{index}本周完成接口联调、测试记录整理和文档归档，下一次评审需要准备运行日志。"
        if index == size // 2:
            content += "该项目的专属识别码是海棠红。"
        memories.append(
            Memory(
                memory_id=f"memory-{index}",
                session_id="origin",
                tier="long",
                scope_type="user",
                scope_id="owner",
                subject=f"项目{index}",
                predicate="评审说明",
                value=content,
                content=content,
                evidence=[Evidence(turn_id=f"t-{index}", event_index=0, quote=content)],
            )
        )
    session = Session(session_id="benchmark")
    query = "海棠红"
    full = Retriever(Settings(), projection=FullScanProjection())
    with tempfile.TemporaryDirectory() as folder:
        indexed = Retriever(Settings(), projection=RetrievalProjection(Path(folder) / "index.sqlite3"))
        try:
            begin = time.perf_counter()
            initial = await indexed.search(query, session, [], memories)
            cold_ms = (time.perf_counter() - begin) * 1000
            samples = {"full_scan": [], "fts_warm": []}
            for _ in range(repeats):
                for name, retriever in (("full_scan", full), ("fts_warm", indexed)):
                    begin = time.perf_counter()
                    result = await retriever.search(query, session, [], memories)
                    samples[name].append((time.perf_counter() - begin) * 1000)
                    assert [hit.chunk_id for hit in result.results] == [
                        hit.chunk_id for hit in initial.results
                    ]
            embedder = CountingEmbedder()
            indexed.embedder = embedder
            await indexed.search(query, session, [], memories)
            cold_inputs = embedder.input_count
            await indexed.search(query, session, [], memories)
            warm_inputs = embedder.input_count - cold_inputs
            return {
                "documents": size,
                "repeats": repeats,
                "query": query,
                "cold_indexed_ms": round(cold_ms, 2),
                "samples_ms": {key: [round(value, 2) for value in values] for key, values in samples.items()},
                "median_ms": {key: round(statistics.median(values), 2) for key, values in samples.items()},
                "selected_ids": [hit.metadata["memory_id"] for hit in initial.results],
                "projection_trace": [warning for warning in initial.warnings if "fts5_projection" in warning],
                "embedding_input_counts": {"synthetic_cold": cold_inputs, "synthetic_warm": warm_inputs},
                "limitations": [
                    "Synthetic selective query, one process, no network or genuine embedding model.",
                    "Full owner snapshot is still loaded and hashed; semantic cosine still scans eligible vectors.",
                    "The FTS index narrows Python BM25 candidates; it does not implement ANN.",
                    "Same-session searches serialize snapshot sync and recall for consistency.",
                ],
            }
        finally:
            full.close()
            indexed.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=10000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path("docs/INDEX_BENCHMARK.json"))
    args = parser.parse_args()
    if args.size < 1 or args.repeats < 1:
        parser.error("size and repeats must be positive")
    report = asyncio.run(benchmark(args.size, args.repeats))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
