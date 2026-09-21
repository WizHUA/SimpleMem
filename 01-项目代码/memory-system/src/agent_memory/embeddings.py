"""Optional OpenAI-compatible embeddings with bounded, content-addressed reuse."""

import asyncio
import hashlib
import math
import os
from collections import OrderedDict
from urllib.parse import urlsplit

import httpx

from .settings import Settings


class EmbeddingError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class OpenAICompatibleEmbedder:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 30,
        batch_size: int = 32,
        cache_entries: int = 256,
        client: httpx.AsyncClient | None = None,
    ):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("Embedding base URL must be HTTP(S) without query or fragment")
        if parsed.username or parsed.password:
            raise ValueError("Use MEMORY_EMBEDDING_API_KEY instead of URL credentials")
        if not model.strip():
            raise ValueError("Embedding model name is required")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Embedding timeout must be finite and positive")
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 256:
            raise ValueError("Embedding batch_size must be an integer from 1 to 256")
        if not isinstance(cache_entries, int) or isinstance(cache_entries, bool) or cache_entries < 0:
            raise ValueError("Embedding cache_entries must be a non-negative integer")
        self.endpoint = base_url.rstrip("/") + "/embeddings"
        self.model, self.api_key = model, api_key
        self.timeout, self.batch_size, self.cache_entries = timeout, batch_size, cache_entries
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=timeout)
        self._cache: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._dimension: int | None = None
        self._generation = 0
        self._lock = asyncio.Lock()

    def clear_cache(self) -> None:
        # Do not let a response already in flight repopulate a cleared cache.
        self._generation += 1
        self._cache.clear()

    async def aclose(self) -> None:
        self.clear_cache()
        if self._owns_client:
            await self.client.aclose()

    async def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Embedding inputs must be non-empty strings")
        # One deadline includes queueing and every batch, not a fresh budget per batch.
        async with asyncio.timeout(self.timeout), self._lock:
            generation = self._generation
            keys = [hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts]
            vectors: dict[str, tuple[float, ...]] = {}
            missing = {}
            for key, text in zip(keys, texts):
                if key in self._cache:
                    vectors[key] = self._cache[key]
                    self._cache.move_to_end(key)
                else:
                    missing.setdefault(key, text)
            items = list(missing.items())
            for start in range(0, len(items), self.batch_size):
                batch = items[start : start + self.batch_size]
                rows = await self._request([text for _, text in batch])
                for (key, _), vector in zip(batch, rows):
                    vectors[key] = vector
            if self._generation == generation:
                for key in keys:
                    self._cache[key] = vectors[key]
                    self._cache.move_to_end(key)
                while len(self._cache) > self.cache_entries:
                    self._cache.popitem(last=False)
            # Return copies: callers cannot mutate cached vectors.
            return [list(vectors[key]) for key in keys]

    async def _request(self, texts: list[str]) -> list[tuple[float, ...]]:
        response = await self.client.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
            json={"model": self.model, "input": texts, "encoding_format": "float"},
            timeout=self.timeout,
        )
        if response.is_error:
            raise EmbeddingError(
                f"Embedding provider request failed with HTTP {response.status_code}", response.status_code
            )
        try:
            rows = response.json()["data"]
            if not isinstance(rows, list) or len(rows) != len(texts):
                raise ValueError("wrong vector count")
            ordered: list[tuple[float, ...] | None] = [None] * len(texts)
            dimension = self._dimension
            for row in rows:
                index, vector = row["index"], row["embedding"]
                if type(index) is not int or not 0 <= index < len(texts) or ordered[index] is not None:
                    raise ValueError("invalid embedding index")
                if not isinstance(vector, list) or not 1 <= len(vector) <= 16384:
                    raise ValueError("invalid embedding dimension")
                if any(type(value) not in {int, float} or not math.isfinite(value) for value in vector):
                    raise ValueError("invalid embedding coordinate")
                if dimension is None:
                    dimension = len(vector)
                if len(vector) != dimension:
                    raise ValueError("inconsistent embedding dimension")
                norm = math.hypot(*vector)
                if not math.isfinite(norm) or norm == 0:
                    raise ValueError("invalid embedding norm")
                ordered[index] = tuple(value / norm for value in vector)
            self._dimension = dimension
            return ordered
        except (ValueError, KeyError, TypeError, OverflowError) as error:
            # Do not echo provider bodies, input texts, credentials or arbitrary URLs.
            raise EmbeddingError("Embedding provider returned invalid vectors") from error


def build_embedder(settings: Settings) -> OpenAICompatibleEmbedder | None:
    base_url = os.getenv("MEMORY_EMBEDDING_BASE_URL", "").strip()
    model = os.getenv("MEMORY_EMBEDDING_NAME", "").strip()
    api_key = os.getenv("MEMORY_EMBEDDING_API_KEY", "").strip()
    if not any((base_url, model, api_key)):
        return None
    if not base_url or not model:
        raise ValueError(
            "Embedding configuration requires MEMORY_EMBEDDING_BASE_URL and MEMORY_EMBEDDING_NAME"
        )
    return OpenAICompatibleEmbedder(
        base_url=base_url, model=model, api_key=api_key or None, timeout=settings.model_timeout
    )
