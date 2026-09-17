"""Only the expensive/external capabilities are abstracted."""

from typing import Protocol

from .models import ExtractionResult, ExtractionWindow, QueryPlan


class ModelNotConfigured(RuntimeError):
    pass


class ModelRequestError(RuntimeError):
    pass


class ConflictError(ValueError):
    pass


class NotFoundError(LookupError):
    pass


class MemoryModel(Protocol):
    async def extract(self, window: ExtractionWindow) -> ExtractionResult: ...
    async def plan(self, query: str, context: dict) -> QueryPlan: ...
    async def answer(self, prompt: str) -> str: ...


class Embedder(Protocol):
    async def encode(self, texts: list[str]) -> list[list[float]]: ...
