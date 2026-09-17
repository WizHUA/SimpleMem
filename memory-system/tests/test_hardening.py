"""Isolation, transaction, configuration and lifecycle regression coverage."""

import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from agent_memory.api import create_app
from agent_memory.models import (
    Candidate,
    Event,
    Evidence,
    EvolutionInput,
    ExtractionResult,
    Scope,
    StatePatch,
    TurnInput,
    utcnow,
)
from agent_memory.ports import ConflictError, NotFoundError
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings
from agent_memory.store import SQLiteStore


class StoreHardeningTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "test.db"
        self.store = SQLiteStore(self.path)
        self.scope = Scope(tenant_id="tenant", owner_id="alice")
        self.session = self.store.create_session(self.scope, project_id="p")

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def append(self, request_id="one", session=None):
        return self.store.append_turn(
            self.scope,
            (session or self.session).session_id,
            TurnInput(request_id=request_id, events=[Event(role="user", content="请长期记忆：默认使用中文")]),
        )

    def candidate(self, turn, **kwargs):
        return Candidate(
            content="默认使用中文",
            subject="用户",
            predicate="语言",
            value="中文",
            scope_type="user",
            durable=True,
            evidence=[Evidence(turn_id=turn.turn_id, event_index=0, quote="默认使用中文")],
            **kwargs,
        )

    def test_two_connections_assign_unique_sequences_and_idempotent_turn(self):
        other = SQLiteStore(self.path)
        try:

            def append(index):
                store = self.store if index % 2 else other
                return store.append_turn(
                    self.scope,
                    self.session.session_id,
                    TurnInput(
                        request_id=f"request-{index}",
                        events=[Event(role="user", content="hello")],
                    ),
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                turns = list(pool.map(append, range(32)))
            self.assertEqual(sorted(t.sequence for t in turns), list(range(1, 33)))
            self.assertEqual(self.store.get_session(self.scope, self.session.session_id).revision, 32)
        finally:
            other.close()

    def test_extraction_cannot_skip_or_forge_pending_turns(self):
        one, two = self.append("one"), self.append("two")
        session = self.store.get_session(self.scope, self.session.session_id)
        for invalid in ([], [two], [one.model_copy(update={"sequence": 99})]):
            with self.assertRaises(ValueError):
                self.store.commit_extraction(self.scope, session, invalid, ExtractionResult())
        self.assertEqual(self.store.get_session(self.scope, session.session_id).processed_sequence, 0)

    def test_stale_extraction_rolls_back_without_memories(self):
        turn = self.append()
        session = self.store.get_session(self.scope, self.session.session_id)
        self.append("second")
        with self.assertRaises(ConflictError):
            self.store.commit_extraction(
                self.scope, session, [turn], ExtractionResult(candidates=[self.candidate(turn)])
            )
        self.assertEqual(self.store.memories(self.scope), [])

    def test_cross_session_direct_long_memory_merges_evidence(self):
        ids = []
        for session in (self.session, self.store.create_session(self.scope)):
            turn = self.append(session=session)
            current = self.store.get_session(self.scope, session.session_id)
            memory = self.store.commit_extraction(
                self.scope, current, [turn], ExtractionResult(candidates=[self.candidate(turn)])
            )[0]
            ids.append(memory.memory_id)
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(len(self.store.memories(self.scope)[0].evidence), 2)

    def test_pending_candidate_can_be_retracted(self):
        turn = self.append()
        memory = self.store.add_candidates(self.scope, self.session.session_id, [self.candidate(turn)])[0]
        self.store.evolve(self.scope, memory.memory_id, EvolutionInput(action="defer", expected_version=1))
        retracted = self.store.evolve(
            self.scope, memory.memory_id, EvolutionInput(action="retract", expected_version=1)
        )
        self.assertEqual(retracted.status, "retracted")

    def test_merge_audit_records_target_before_state_not_consumed_candidate(self):
        first = self.append("one")
        target = self.store.add_candidates(self.scope, self.session.session_id, [self.candidate(first)])[0]
        self.store.evolve(self.scope, target.memory_id, EvolutionInput(action="promote", expected_version=1))
        second = self.append("two")
        source = self.store.add_candidates(self.scope, self.session.session_id, [self.candidate(second)])[0]
        self.store.evolve(
            self.scope,
            source.memory_id,
            EvolutionInput(action="merge", expected_version=1, target_id=target.memory_id, target_version=1),
        )
        audit = self.store.history(self.scope, target.memory_id)[-1]
        before, after = json.loads(audit["before_json"]), json.loads(audit["after_json"])
        self.assertEqual(before["memory_id"], target.memory_id)
        self.assertEqual(after["memory_id"], target.memory_id)
        self.assertEqual(len(before["evidence"]), 1)
        self.assertEqual(len(after["evidence"]), 2)
        self.assertEqual(self.store.get_memory(self.scope, source.memory_id).status, "superseded")

    def test_hierarchy_excludes_future_and_expired_facts(self):
        turn = self.append()
        candidates = [
            self.candidate(turn, valid_from=utcnow() + timedelta(days=1)),
            self.candidate(turn, valid_to=utcnow() - timedelta(days=1)),
        ]
        session = self.store.get_session(self.scope, self.session.session_id)
        self.store.commit_extraction(self.scope, session, [turn], ExtractionResult(candidates=candidates))
        self.assertEqual(self.store.hierarchy(self.scope)["episode_count"], 0)

    def test_delete_owner_erases_audit_while_preserving_other_tenants(self):
        turn = self.append()
        self.store.add_candidates(self.scope, self.session.session_id, [self.candidate(turn)])
        other = Scope(tenant_id="other", owner_id="alice")
        preserved = self.store.create_session(other)
        deleted = self.store.delete_owner(self.scope)
        self.assertEqual(deleted["sessions"], 1)
        self.assertGreater(deleted["audit"], 0)
        self.assertEqual(self.store.memories(self.scope), [])
        self.assertEqual(self.store.get_session(other, preserved.session_id).session_id, preserved.session_id)
        with self.assertRaises(NotFoundError):
            self.store.get_session(self.scope, self.session.session_id)

    def test_online_backup_restores_watermark_versions_evidence_and_isolation(self):
        turn = self.append()
        session = self.store.get_session(self.scope, self.session.session_id)
        memory = self.store.commit_extraction(
            self.scope, session, [turn], ExtractionResult(candidates=[self.candidate(turn)])
        )[0]
        other = Scope(tenant_id="tenant", owner_id="bob")
        self.store.create_session(other)
        backup_path = Path(self.folder.name) / "backups" / "recovery.db"
        self.store.backup_to(backup_path)
        with self.assertRaises(FileExistsError):
            self.store.backup_to(backup_path)
        with self.assertRaises(FileExistsError):
            self.store.backup_to(self.path)
        self.store.delete_owner(self.scope)
        restored = SQLiteStore(backup_path)
        try:
            restored_session, restored_turns = restored.session_snapshot(self.scope, session.session_id)
            self.assertEqual(restored_session.processed_sequence, 1)
            self.assertEqual(restored_turns, [turn])
            self.assertEqual(restored.get_memory(self.scope, memory.memory_id), memory)
            self.assertEqual(len(restored.history(self.scope, memory.memory_id)), 1)
            with self.assertRaises(NotFoundError):
                restored.get_memory(other, memory.memory_id)
            with self.assertRaises(NotFoundError):
                restored.get_session(other, session.session_id)
        finally:
            restored.close()

    def test_generation_version_tracks_local_turns_and_owner_memory_edits(self):
        before = self.store.generation_version(self.scope, self.session.session_id)
        turn = self.append()
        written = self.store.generation_version(self.scope, self.session.session_id)
        self.assertGreater(written[0], before[0])
        memory = self.store.add_candidates(self.scope, self.session.session_id, [self.candidate(turn)])[0]
        extracted = self.store.generation_version(self.scope, self.session.session_id)
        self.assertGreater(extracted[1], written[1])
        self.store.evolve(self.scope, memory.memory_id, EvolutionInput(action="retract", expected_version=1))
        self.assertGreater(
            self.store.generation_version(self.scope, self.session.session_id)[1], extracted[1]
        )
        self.store.delete_owner(self.scope)
        with self.assertRaises(NotFoundError):
            self.store.generation_version(self.scope, self.session.session_id)


class BoundaryTests(unittest.TestCase):
    def test_http_zero_context_window_does_not_return_all_turns(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = MemoryRuntime(Settings(db_path=Path(folder) / "test.db", context_turns=0))
            try:
                with TestClient(create_app(runtime=runtime)) as client:
                    session = client.post("/api/v1/sessions", json={}).json()["session_id"]
                    client.post(
                        f"/api/v1/sessions/{session}/turns",
                        json={
                            "request_id": "one",
                            "events": [{"role": "user", "content": "private raw turn"}],
                        },
                    )
                    state = client.get(f"/api/v1/sessions/{session}").json()
                    self.assertEqual(state["recent_turns"], [])
                    self.assertEqual(state["pending_turns"], 1)
            finally:
                asyncio.run(runtime.close())

    def test_real_store_concurrent_search_scope_isolation(self):
        async def scenario(folder):
            runtime = MemoryRuntime(Settings(db_path=Path(folder) / "test.db"))
            identities = [
                Scope(tenant_id="t1", owner_id="alice"),
                Scope(tenant_id="t1", owner_id="bob"),
                Scope(tenant_id="t2", owner_id="alice"),
            ]
            sessions = []
            try:
                for index, scope in enumerate(identities):
                    session = runtime.store.create_session(scope)
                    sessions.append(session)
                    await runtime.append_turn(
                        scope,
                        session.session_id,
                        TurnInput(
                            request_id="one",
                            events=[Event(role="user", content=f"private secret code is code-{index}")],
                        ),
                    )
                results = await asyncio.gather(
                    *(
                        runtime.search(scope, session.session_id, "private secret code")
                        for scope, session in zip(identities, sessions)
                    )
                )
                for index, result in enumerate(results):
                    self.assertTrue(result.results)
                    self.assertTrue(all(f"code-{index}" in hit.content for hit in result.results))
                for index, scope in enumerate(identities):
                    with self.assertRaises(NotFoundError):
                        await runtime.search(
                            scope, sessions[(index + 1) % len(sessions)].session_id, "private secret code"
                        )
            finally:
                await runtime.close()

        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(scenario(folder))

    def test_settings_reject_invalid_direct_configuration(self):
        for kwargs in (
            {"pending_turns": 0},
            {"context_turns": -1},
            {"model_timeout": float("nan")},
            {"model_timeout": float("inf")},
            {"inference_cache_ttl_seconds": float("nan")},
            {"deployment_mode": "production", "api_key": ""},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Settings(**kwargs)
        self.assertEqual(Settings(context_turns=0).context_turns, 0)

    def test_bearer_auth_protects_data_and_ignores_scope_headers(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = MemoryRuntime(
                Settings(db_path=Path(folder) / "test.db", api_key="s" * 32, deployment_mode="production")
            )
            try:
                with TestClient(create_app(runtime=runtime)) as client:
                    self.assertEqual(client.get("/health").status_code, 200)
                    self.assertEqual(client.get("/api/v1/memories").status_code, 401)
                    self.assertEqual(
                        client.post(
                            "/api/v1/sessions", json={}, headers={"Authorization": "Bearer wrong"}
                        ).status_code,
                        401,
                    )
                    headers = {"Authorization": "Bearer " + "s" * 32, "X-Tenant-ID": "attacker"}
                    response = client.post("/api/v1/sessions", json={}, headers=headers)
                    self.assertEqual(response.status_code, 201)
                    scope = Scope(
                        tenant_id=runtime.settings.local_tenant, owner_id=runtime.settings.local_owner
                    )
                    runtime.store.get_session(scope, response.json()["session_id"])
                    self.assertEqual(client.delete("/api/v1/owner/data").status_code, 401)
                    self.assertEqual(client.delete("/api/v1/owner/data", headers=headers).status_code, 200)
            finally:
                asyncio.run(runtime.close())

    def test_zero_context_and_oversized_extraction_state(self):
        async def scenario(folder):
            class Model:
                async def extract(self, window):
                    return ExtractionResult(state_patch=StatePatch(goal="巨大状态" * 10000))

            runtime = MemoryRuntime(
                Settings(db_path=Path(folder) / "test.db", context_turns=0), model=Model()
            )
            scope = Scope(tenant_id="t", owner_id="o")
            try:
                session = runtime.store.create_session(scope)
                turn = runtime.store.append_turn(
                    scope,
                    session.session_id,
                    TurnInput(request_id="1", events=[Event(role="user", content="hello")]),
                )
                current = runtime.store.get_session(scope, session.session_id)
                runtime.store.commit_extraction(scope, current, [turn], ExtractionResult())
                window = await runtime.short_term.window(scope, session.session_id)
                self.assertEqual(window.context_turns, [])
                runtime.store.append_turn(
                    scope,
                    session.session_id,
                    TurnInput(request_id="2", events=[Event(role="user", content="second")]),
                )
                with self.assertRaises(ValueError):
                    await runtime.short_term.extract(scope, session.session_id)
                self.assertEqual(runtime.store.get_session(scope, session.session_id).processed_sequence, 1)
            finally:
                await runtime.close()

        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(scenario(folder))
