"""Projection indexes accelerate recall without becoming an authorization source."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_memory.models import Evidence, Memory, Session
from agent_memory.ports import ConflictError
from agent_memory.retrieval import Retriever, _terms
from agent_memory.retrieval_projection import RetrievalProjection
from agent_memory.settings import Settings


def fact(identifier="m1", content="用户偏好中文", **changes):
    return Memory(
        memory_id=identifier,
        session_id="s",
        scope_type="user",
        scope_id="owner",
        tier="long",
        subject="用户",
        predicate="偏好",
        value=content,
        content=content,
        evidence=[Evidence(turn_id="t", event_index=0, quote=content)],
        **changes,
    )


class CountingEmbedder:
    endpoint = "https://embeddings.example.test/v1/embeddings"
    model = "test-embedding-v1"

    def __init__(self):
        self.inputs = []

    async def encode(self, texts):
        self.inputs.extend(texts)
        return [[1.0, 0.0] for _ in texts]


class ProjectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.projection = RetrievalProjection()
        self.addCleanup(self.projection.close)

    def test_sync_only_tokenizes_changes_and_removes_deleted_versions(self):
        calls = []

        def tokenize(text):
            calls.append(text)
            return _terms(text)

        item = fact()
        self.projection.sync("owner", [item], tokenize, 0)
        self.projection.sync("owner", [item], tokenize, 0)
        self.assertEqual(len(calls), 1)
        updated = item.model_copy(update={"version": 2, "content": "用户偏好英文"})
        self.projection.sync("owner", [updated], tokenize, 0)
        self.assertEqual(self.projection.candidates("owner", {"中文"}, [item], 10, 0), [])
        self.assertEqual(
            self.projection.candidates("owner", {"英文"}, [updated], 10, 0), [self.projection.key(updated)]
        )

    def test_fts_scope_filter_and_quoted_query_operators(self):
        a, b = fact("same", "中文"), fact("same", "英文")
        self.projection.sync("a", [a], _terms, 0)
        self.projection.sync("b", [b], _terms, 0)
        self.assertEqual(self.projection.candidates("a", {"英文"}, [a], 10, 0), [])
        self.assertEqual(self.projection.candidates("a", {'" OR * - NEAR('}, [a], 10, 0), [])
        self.assertEqual(self.projection.candidates("b", {"英文"}, [], 10, 0), [])
        self.assertEqual(self.projection.candidates("b", {"英文"}, [b], 10, 0), [self.projection.key(b)])

    async def test_persisted_vectors_survive_restart_and_encode_only_new_content(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "projection.sqlite3"
            embedder = CountingEmbedder()
            first = Retriever(Settings(), embedder=embedder, projection=RetrievalProjection(path))
            await first.search("偏好", Session(session_id="s"), [], [fact()], projection_scope="a")
            first.close()
            embedder.inputs.clear()
            second = Retriever(Settings(), embedder=embedder, projection=RetrievalProjection(path))
            try:
                await second.search(
                    "偏好",
                    Session(session_id="s"),
                    [],
                    [fact(), fact("new", "用户喜欢咖啡")],
                    projection_scope="a",
                )
                self.assertEqual(embedder.inputs, ["偏好", "用户喜欢咖啡"])
            finally:
                second.close()

    async def test_embedding_model_endpoint_and_revision_invalidate_vectors(self):
        embedder = CountingEmbedder()
        retriever = Retriever(Settings(), embedder=embedder, projection=self.projection)

        async def run():
            embedder.inputs.clear()
            await retriever.search("偏好", Session(session_id="s"), [], [fact()], projection_scope="a")
            return len(embedder.inputs)

        self.assertEqual(await run(), 2)
        self.assertEqual(await run(), 1)
        embedder.model = "different-model"
        self.assertEqual(await run(), 2)
        embedder.endpoint = "https://another.example.test/embeddings"
        self.assertEqual(await run(), 2)
        with patch.dict(os.environ, {"MEMORY_EMBEDDING_REVISION": "new-weights"}):
            self.assertEqual(await run(), 2)
        await retriever.search("偏好", Session(session_id="s"), [], [fact()], projection_scope="other-owner")
        self.assertEqual(embedder.inputs[-2:], ["偏好", "用户偏好中文"])

    async def test_clear_prevents_inflight_vector_repopulation(self):
        started, release = asyncio.Event(), asyncio.Event()

        class WaitingEmbedder(CountingEmbedder):
            async def encode(self, texts):
                started.set()
                await release.wait()
                return await super().encode(texts)

        retriever = Retriever(Settings(), embedder=WaitingEmbedder(), projection=self.projection)
        task = asyncio.create_task(
            retriever.search("偏好", Session(session_id="s"), [], [fact()], projection_scope="a")
        )
        await started.wait()
        retriever.clear_projection("a")
        release.set()
        with self.assertRaises(ConflictError):
            await task
        self.assertEqual(self.projection.candidates("a", {"中文"}, [fact()], 10, 1), [])
        self.assertEqual(
            self.projection._connection.execute("SELECT COUNT(*) FROM projection_vectors").fetchone()[0], 0
        )

    def test_rebuild_and_delete_remove_all_owner_vectors_but_not_other_owner(self):
        self.projection.sync("a", [fact()], _terms, 0)
        digest = self.projection.digest(fact().content)
        self.projection.save_vectors("a", "model", {digest: [1.0]}, 0)
        self.projection.save_vectors("b", "model", {digest: [1.0]}, 0)
        self.projection.sync("a", [], _terms, 0)
        self.assertEqual(self.projection.vectors("a", "model", [digest], 0), {})
        self.assertEqual(self.projection.vectors("b", "model", [digest], 0), {digest: [1.0]})
        self.projection.rebuild("a", [fact()], _terms)
        self.assertEqual(
            self.projection.candidates("a", {"中文"}, [fact()], 10, 1), [self.projection.key(fact())]
        )

    async def test_fts_reduces_large_library_before_python_ranking(self):
        retriever = Retriever(Settings(), projection=self.projection)
        items = [fact(f"n{i}", f"项目{i}资料归档") for i in range(200)] + [fact("target", "罕见暗号蓝鲸")]
        result = await retriever.search("蓝鲸", Session(session_id="s"), [], items)
        self.assertEqual([h.metadata["memory_id"] for h in result.results], ["target"])
        self.assertIn("fts5_projection: reranked=1; eligible=201", result.warnings)

    async def test_parallel_sessions_keep_separate_short_memory_snapshots(self):
        retriever = Retriever(Settings(), projection=self.projection)

        async def run(session):
            item = fact(session, f"用户偏好{session}").model_copy(
                update={"scope_type": "session", "scope_id": session, "session_id": session, "tier": "short"}
            )
            return await retriever.search(
                "偏好",
                Session(session_id=session),
                [item],
                [],
                projection_scope=retriever.projection_namespace("t", "owner", session),
            )

        results = await asyncio.gather(run("甲"), run("乙"))
        self.assertEqual([[h.metadata["memory_id"] for h in r.results] for r in results], [["甲"], ["乙"]])
        retriever.clear_owner("t", "owner")
        count = self.projection._connection.execute("SELECT COUNT(*) FROM projection_docs").fetchone()[0]
        self.assertEqual(count, 0)

    async def test_same_namespace_serializes_snapshot_sync_through_search(self):
        first_entered, release = asyncio.Event(), asyncio.Event()

        class BlockingRetriever(Retriever):
            async def _search(self, *args):
                if not first_entered.is_set():
                    first_entered.set()
                    await release.wait()
                return await super()._search(*args)

        retriever = BlockingRetriever(Settings(), projection=self.projection)
        first = asyncio.create_task(
            retriever.search("中文", Session(session_id="s"), [], [fact("first", "用户偏好中文")])
        )
        await first_entered.wait()
        second = asyncio.create_task(
            retriever.search("英文", Session(session_id="s"), [], [fact("second", "用户偏好英文")])
        )
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(first, second)
        self.assertEqual(
            [[h.metadata["memory_id"] for h in r.results] for r in results], [["first"], ["second"]]
        )
