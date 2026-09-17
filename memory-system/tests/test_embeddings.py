"""Embedding transport, vector integrity, bounded reuse and explicit configuration."""

import asyncio
import json

import httpx
import pytest

from agent_memory.embeddings import EmbeddingError, OpenAICompatibleEmbedder, build_embedder
from agent_memory.settings import Settings


def test_batch_order_deduplication_lru_and_immutable_vectors():
    async def scenario():
        calls = []

        def handle(request):
            body = json.loads(request.content)
            calls.append(body["input"])
            assert request.url.path == "/v1/embeddings"
            assert request.headers["Authorization"] == "Bearer private-key"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": index, "embedding": [len(text), 1]}
                        for index, text in reversed(list(enumerate(body["input"])))
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            embedder = OpenAICompatibleEmbedder(
                base_url="https://embeddings.invalid/v1",
                model="embed",
                api_key="private-key",
                batch_size=2,
                cache_entries=2,
                client=client,
            )
            vectors = await embedder.encode(["a", "bb", "a", "ccc"])
            assert calls == [["a", "bb"], ["ccc"]]
            assert vectors[0] == vectors[2]
            assert vectors[1][0] > vectors[0][0]
            vectors[0][0] = 123
            assert (await embedder.encode(["a"]))[0][0] != 123
            assert len(calls) == 2
            await embedder.encode(["bb"])
            assert calls[-1] == ["bb"]
            assert len(embedder._cache) == 2
            embedder.clear_cache()
            await embedder.encode(["bb"])
            assert len(calls) == 4
            await embedder.aclose()
            assert not client.is_closed

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "data",
    [
        [],
        [{"index": 0, "embedding": []}],
        [{"index": 0, "embedding": [0, 0]}],
        [{"index": 1, "embedding": [1, 2]}],
        [{"index": True, "embedding": [1, 2]}],
        [{"index": 0, "embedding": [True, 2]}],
        [{"index": 0, "embedding": ["1", 2]}],
        [{"index": 0, "embedding": [1, 2]}, {"index": 0, "embedding": [1, 2]}],
    ],
)
def test_invalid_embedding_envelopes_fail_without_fallback(data):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": data}))
        ) as client:
            embedder = OpenAICompatibleEmbedder(
                base_url="https://example.invalid", model="embed", client=client
            )
            with pytest.raises(EmbeddingError, match="invalid vectors"):
                await embedder.encode(["confidential input"])
            assert not embedder._cache

    asyncio.run(scenario())


def test_non_finite_coordinates_and_dimension_drift_are_rejected():
    async def scenario():
        response_bodies = iter(
            [
                '{"data":[{"index":0,"embedding":[NaN,1]}]}',
                '{"data":[{"index":0,"embedding":[1,2]}]}',
                '{"data":[{"index":0,"embedding":[1,2,3]}]}',
            ]
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=next(response_bodies)))
        ) as client:
            embedder = OpenAICompatibleEmbedder(
                base_url="https://example.invalid", model="embed", client=client
            )
            with pytest.raises(EmbeddingError):
                await embedder.encode(["one"])
            await embedder.encode(["two"])
            with pytest.raises(EmbeddingError):
                await embedder.encode(["three"])
            assert len(embedder._cache) == 1

    asyncio.run(scenario())


def test_provider_failure_never_echoes_sensitive_response():
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(401, text="secret private provider detail")
            )
        ) as client:
            embedder = OpenAICompatibleEmbedder(
                base_url="https://example.invalid", model="embed", client=client
            )
            with pytest.raises(EmbeddingError) as caught:
                await embedder.encode(["private user text"])
            assert caught.value.status_code == 401
            assert "secret" not in str(caught.value)
            assert "user text" not in str(caught.value)

    asyncio.run(scenario())


def test_timeout_covers_all_batches_and_clear_during_flight_prevents_repopulation():
    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()

        async def handle(request):
            started.set()
            await finish.wait()
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 2]}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            embedder = OpenAICompatibleEmbedder(
                base_url="https://example.invalid", model="embed", client=client, timeout=0.01
            )
            with pytest.raises(TimeoutError):
                await embedder.encode(["timeout"])
            assert not embedder._cache
            embedder.timeout = 1
            started.clear()
            task = asyncio.create_task(embedder.encode(["in-flight"]))
            await started.wait()
            embedder.clear_cache()
            finish.set()
            assert len(await task) == 1
            assert not embedder._cache

    asyncio.run(scenario())


def test_explicit_embedding_config_does_not_inherit_chat(monkeypatch):
    for key in ("MEMORY_EMBEDDING_BASE_URL", "MEMORY_EMBEDDING_NAME", "MEMORY_EMBEDDING_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MEMORY_MODEL_BASE_URL", "https://chat.invalid")
    monkeypatch.setenv("MEMORY_MODEL_NAME", "chat-only")
    assert build_embedder(Settings()) is None
    monkeypatch.setenv("MEMORY_EMBEDDING_API_KEY", "configured-only-key")
    with pytest.raises(ValueError, match="EMBEDDING_BASE_URL"):
        build_embedder(Settings())
    monkeypatch.setenv("MEMORY_EMBEDDING_BASE_URL", "https://embed.invalid/v1")
    monkeypatch.setenv("MEMORY_EMBEDDING_NAME", "embed")

    async def scenario():
        embedder = build_embedder(Settings())
        assert embedder.endpoint == "https://embed.invalid/v1/embeddings"
        assert embedder.model == "embed"
        await embedder.aclose()

    asyncio.run(scenario())
