"""Thin HTTP transport. Replace get_scope with host authentication when embedding."""

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import Field

from .models import Contract, EvolutionInput, Scope, TurnInput
from .ports import ConflictError, ModelNotConfigured, NotFoundError
from .runtime import MemoryRuntime
from .settings import Settings


class CreateSession(Contract):
    topic: str = Field(default="", max_length=200)
    project_id: str | None = Field(default=None, max_length=100)


class Query(Contract):
    session_id: str
    query: str = Field(min_length=1, max_length=3000)
    top_k: int = Field(default=10, ge=0, le=100)
    timeout: float = Field(default=30.0, gt=0, le=120)


def get_runtime(request: Request) -> MemoryRuntime:
    return request.app.state.runtime


RuntimeDep = Annotated[MemoryRuntime, Depends(get_runtime)]


def get_scope(runtime: RuntimeDep) -> Scope:
    # Intentionally not taken from untrusted headers/query/body fields.
    return Scope(tenant_id=runtime.settings.local_tenant, owner_id=runtime.settings.local_owner)


ScopeDep = Annotated[Scope, Depends(get_scope)]


def create_app(settings: Settings | None = None, runtime: MemoryRuntime | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = runtime or MemoryRuntime.from_settings(settings)
        yield
        if runtime is None:
            await app.state.runtime.close()

    app = FastAPI(title="Agent Memory Backend", version="0.1.0", lifespan=lifespan)
    origins = [v.strip() for v in os.getenv("MEMORY_ALLOWED_ORIGINS", "").split(",") if v.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "PATCH"],
            allow_headers=["Content-Type"],
        )

    async def error_response(request: Request, exc: Exception):
        status = 500
        if isinstance(exc, NotFoundError):
            status = 404
        elif isinstance(exc, ConflictError):
            status = 409
        elif isinstance(exc, ModelNotConfigured):
            status = 503
        elif isinstance(exc, TimeoutError):
            status = 504
        elif isinstance(exc, ValueError):
            status = 400
        return JSONResponse(status_code=status, content={"error": type(exc).__name__, "detail": str(exc)})

    for error_type in (NotFoundError, ConflictError, ModelNotConfigured, TimeoutError, ValueError):
        app.add_exception_handler(error_type, error_response)

    @app.get("/health")
    async def health(service: RuntimeDep):
        return await service.health()

    @app.post("/api/v1/sessions", status_code=201)
    async def create_session(body: CreateSession, scope: ScopeDep, service: RuntimeDep):
        return await asyncio.to_thread(service.store.create_session, scope, body.topic, body.project_id)

    @app.get("/api/v1/sessions/{session_id}")
    async def session_state(session_id: str, scope: ScopeDep, service: RuntimeDep):
        session = await asyncio.to_thread(service.store.get_session, scope, session_id)
        turns = await asyncio.to_thread(service.store.turns, scope, session_id)
        memories = await asyncio.to_thread(service.store.memories, scope, session_id, "short")
        return {
            "session": session,
            "recent_turns": turns[-service.settings.context_turns :],
            "short_memories": memories,
            "pending_turns": sum(t.sequence > session.processed_sequence for t in turns),
        }

    @app.post("/api/v1/sessions/{session_id}/turns", status_code=201)
    async def append(
        session_id: str,
        body: TurnInput,
        scope: ScopeDep,
        service: RuntimeDep,
    ):
        return await service.append_turn(scope, session_id, body)

    @app.post("/api/v1/sessions/{session_id}/extract")
    async def extract(session_id: str, scope: ScopeDep, service: RuntimeDep):
        return await service.short_term.extract(scope, session_id)

    @app.post("/api/v1/search")
    async def search(body: Query, scope: ScopeDep, service: RuntimeDep):
        return await service.search(scope, body.session_id, body.query, body.top_k, body.timeout)

    @app.post("/api/v1/answer")
    async def answer(body: Query, scope: ScopeDep, service: RuntimeDep):
        return await service.answer(scope, body.session_id, body.query, body.top_k, body.timeout)

    @app.get("/api/v1/memories")
    async def memories(scope: ScopeDep, service: RuntimeDep):
        return await asyncio.to_thread(service.store.memories, scope)

    @app.post("/api/v1/memories/{memory_id}/evolve")
    async def evolve(
        memory_id: str,
        body: EvolutionInput,
        scope: ScopeDep,
        service: RuntimeDep,
    ):
        return await service.long_term.evolve(scope, memory_id, body)

    @app.get("/api/v1/memories/{memory_id}/history")
    async def history(memory_id: str, scope: ScopeDep, service: RuntimeDep):
        return await asyncio.to_thread(service.store.history, scope, memory_id)

    @app.post("/api/v1/maintenance")
    async def maintain(scope: ScopeDep, service: RuntimeDep):
        return await service.long_term.maintain(scope)

    @app.get("/api/v1/groups")
    async def groups(scope: ScopeDep, service: RuntimeDep):
        return await asyncio.to_thread(service.store.groups, scope)

    return app
