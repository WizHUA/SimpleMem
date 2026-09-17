"""Thin HTTP transport. Replace get_scope with host authentication when embedding."""

import asyncio
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field

from . import __version__
from .embeddings import EmbeddingError
from .models import Contract, EvolutionInput, Scope, TurnInput
from .ports import ConflictError, ModelNotConfigured, ModelRequestError, NotFoundError
from .runtime import MemoryRuntime
from .settings import Settings


class CreateSession(Contract):
    topic: str = Field(default="", max_length=200)
    project_id: str | None = Field(default=None, max_length=100)


class Query(Contract):
    session_id: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=3000)
    top_k: int | None = Field(default=None, ge=0, le=100)
    timeout: float = Field(default=180.0, gt=0, le=180)
    accelerate: bool = True


def get_runtime(request: Request) -> MemoryRuntime:
    return request.app.state.runtime


RuntimeDep = Annotated[MemoryRuntime, Depends(get_runtime)]


def get_scope(request: Request, runtime: RuntimeDep) -> Scope:
    # Intentionally not taken from untrusted headers/query/body fields.
    if runtime.settings.api_key:
        authorization = request.headers.get("Authorization", "")
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            credential.encode(), runtime.settings.api_key.encode()
        ):
            raise HTTPException(
                status_code=401, detail="Invalid bearer credential", headers={"WWW-Authenticate": "Bearer"}
            )
    return Scope(tenant_id=runtime.settings.local_tenant, owner_id=runtime.settings.local_owner)


ScopeDep = Annotated[Scope, Depends(get_scope)]


def create_app(settings: Settings | None = None, runtime: MemoryRuntime | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = runtime or MemoryRuntime.from_settings(settings)
        worker = None
        service = app.state.runtime
        if runtime is None and service.settings.maintenance_interval > 0:
            worker = asyncio.create_task(
                service.maintenance_worker(
                    Scope(tenant_id=service.settings.local_tenant, owner_id=service.settings.local_owner)
                )
            )
        try:
            yield
        finally:
            if worker:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            if runtime is None:
                await app.state.runtime.close()

    app = FastAPI(title="Agent Memory Backend", version=__version__, lifespan=lifespan)
    origins = [v.strip() for v in os.getenv("MEMORY_ALLOWED_ORIGINS", "").split(",") if v.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "Authorization"],
        )

    async def error_response(request: Request, exc: Exception):
        status = 500
        detail = str(exc)
        if isinstance(exc, NotFoundError):
            status = 404
        elif isinstance(exc, ConflictError):
            status = 409
        elif isinstance(exc, ModelNotConfigured):
            status = 503
        elif isinstance(exc, TimeoutError):
            status = 504
        elif isinstance(exc, EmbeddingError):
            status = 503 if exc.status_code in (401, 403, 429) else 502
            detail = "Embedding 服务调用失败，请检查 MEMORY_EMBEDDING 配置或稍后重试。"
        elif isinstance(exc, (ModelRequestError, httpx.HTTPStatusError)):
            upstream_status = (
                exc.status_code if isinstance(exc, ModelRequestError) else exc.response.status_code
            )
            status = 503 if upstream_status in (401, 402, 403, 429) else 502
            if upstream_status == 401:
                detail = "模型服务鉴权失败，请检查 MEMORY_MODEL_API_KEY。"
            elif upstream_status == 402:
                detail = "模型服务额度不足或需要付费，请检查账户与计费配置。"
            elif upstream_status == 403:
                detail = "模型服务拒绝访问，请检查 API Key 对当前模型的权限。"
            elif upstream_status == 429:
                detail = "模型服务请求过于频繁，请稍后重试。"
            else:
                detail = f"模型服务请求失败（HTTP {upstream_status}）。"
        elif isinstance(exc, httpx.TimeoutException):
            status = 504
            detail = "模型服务响应超时，请稍后重试。"
        elif isinstance(exc, httpx.RequestError):
            status = 502
            detail = "无法连接模型服务，请检查网络或模型服务地址。"
        elif isinstance(exc, ValueError):
            status = 400
        return JSONResponse(status_code=status, content={"error": type(exc).__name__, "detail": detail})

    for error_type in (
        NotFoundError,
        ConflictError,
        ModelNotConfigured,
        ModelRequestError,
        EmbeddingError,
        TimeoutError,
        ValueError,
        httpx.HTTPStatusError,
        httpx.TimeoutException,
        httpx.RequestError,
    ):
        app.add_exception_handler(error_type, error_response)

    @app.get("/health")
    async def health(service: RuntimeDep):
        return await service.health()

    @app.post("/api/v1/sessions", status_code=201)
    async def create_session(body: CreateSession, scope: ScopeDep, service: RuntimeDep):
        return await asyncio.to_thread(service.store.create_session, scope, body.topic, body.project_id)

    @app.get("/api/v1/sessions")
    async def list_sessions(scope: ScopeDep, service: RuntimeDep):
        return await asyncio.to_thread(service.store.sessions, scope)

    @app.get("/api/v1/sessions/{session_id}")
    async def session_state(session_id: str, scope: ScopeDep, service: RuntimeDep):
        session, turns = await asyncio.to_thread(service.store.session_snapshot, scope, session_id)
        memories = await asyncio.to_thread(service.store.memories, scope, session_id, "short")
        return {
            "session": session,
            "recent_turns": turns[-service.settings.context_turns :]
            if service.settings.context_turns
            else [],
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
    async def extract(session_id: str, scope: ScopeDep, service: RuntimeDep, apply_evolution: bool = True):
        return await service.extract(scope, session_id, apply_evolution=apply_evolution)

    @app.post("/api/v1/search")
    async def search(body: Query, scope: ScopeDep, service: RuntimeDep):
        return await service.search(scope, body.session_id, body.query, body.top_k, body.timeout)

    @app.post("/api/v1/answer")
    async def answer(body: Query, scope: ScopeDep, service: RuntimeDep):
        return await service.answer(
            scope, body.session_id, body.query, body.top_k, body.timeout, accelerate=body.accelerate
        )

    @app.post("/api/v1/sessions/{session_id}/answer/stream")
    async def answer_stream(
        session_id: str, body: Query, request: Request, scope: ScopeDep, service: RuntimeDep
    ):
        if body.session_id != session_id:
            raise ValueError("Body session_id must match URL")
        await asyncio.to_thread(service.store.get_session, scope, session_id)

        async def events():
            queue = asyncio.Queue(maxsize=32)
            started = time.perf_counter()

            async def progress(phase, detail, **data):
                await queue.put(
                    {
                        "type": "stage",
                        "phase": phase,
                        "detail": detail,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                        **data,
                    }
                )

            async def produce():
                try:
                    result = await service.answer(
                        scope,
                        session_id,
                        body.query,
                        body.top_k,
                        body.timeout,
                        accelerate=body.accelerate,
                        progress=progress,
                    )
                    await queue.put({"type": "result", "answer": result.model_dump(mode="json")})
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - terminate streaming transport with a sanitized error
                    response = await error_response(request, exc)
                    payload = json.loads(response.body)
                    await queue.put(
                        {"type": "error", "detail": payload["detail"], "status": response.status_code}
                    )

            task = asyncio.create_task(produce())
            try:
                while True:
                    item = await queue.get()
                    yield json.dumps(item, ensure_ascii=False) + "\n"
                    if item["type"] in {"result", "error"}:
                        break
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/memories")
    async def memories(scope: ScopeDep, service: RuntimeDep):
        return await asyncio.to_thread(service.store.memories, scope)

    @app.get("/api/v1/entities")
    async def entities(scope: ScopeDep, service: RuntimeDep):
        return await asyncio.to_thread(service.store.entities, scope)

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
        return await service.maintain(scope)

    @app.get("/api/v1/groups")
    async def groups(scope: ScopeDep, service: RuntimeDep):
        return await asyncio.to_thread(service.store.groups, scope)

    @app.get("/api/v1/hierarchy")
    async def hierarchy(scope: ScopeDep, service: RuntimeDep):
        return await asyncio.to_thread(service.store.hierarchy, scope)

    @app.delete("/api/v1/owner/data")
    async def delete_owner_data(scope: ScopeDep, service: RuntimeDep):
        deleted = await service.delete_owner(scope)
        return {"status": "deleted", "deleted": deleted}

    return app
