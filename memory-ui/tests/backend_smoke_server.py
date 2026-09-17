"""Real HTTP + SQLite UI smoke. Explicit seed data, no model and no user database."""

from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from agent_memory.api import create_app
from agent_memory.models import Candidate, Event, Evidence, EvolutionInput, Scope, TurnInput
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings

workspace = TemporaryDirectory(prefix="memory-ui-smoke-")
runtime = MemoryRuntime(
    Settings(
        db_path=Path(workspace.name) / "smoke.sqlite3",
        api_key="integration-test-only-token-0123456789",
        local_tenant="ui-smoke",
        local_owner="ui-user",
    ),
    model=None,
    embedder=None,
)
scope = Scope(tenant_id="ui-smoke", owner_id="ui-user")
session = runtime.store.create_session(scope, "真实 HTTP 集成验收", "ui-smoke")
for index, language in enumerate(("中文", "英文")):
    content = f"我偏好{language}说明"
    turn = runtime.store.append_turn(
        scope, session.session_id, TurnInput(request_id=f"seed-{index}", events=[Event(role="user", content=content)])
    )
    memory = runtime.store.add_candidates(
        scope,
        session.session_id,
        [
            Candidate(
                content=f"用户偏好{language}说明",
                subject="用户",
                predicate="说明语言",
                value=language,
                kind="preference",
                durable=True,
                scope_type="user",
                evidence=[Evidence(turn_id=turn.turn_id, event_index=0, quote=content)],
            )
        ],
    )[0]
    if index == 0:
        runtime.store.evolve(scope, memory.memory_id, EvolutionInput(action="promote", expected_version=memory.version))
try:
    uvicorn.run(create_app(runtime=runtime), host="127.0.0.1", port=8095, log_level="warning")
finally:
    runtime.store.close()
    workspace.cleanup()
