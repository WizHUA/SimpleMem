"""HTTP boundary checks using the fixed local-development principal."""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from agent_memory.api import create_app
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        settings = Settings(db_path=Path(self.folder.name) / "api.db")
        self.runtime = MemoryRuntime(settings)
        self.client = TestClient(create_app(settings, self.runtime))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        # An injected runtime is caller-owned, so the app deliberately did not close it.
        self.runtime.store.close()
        self.folder.cleanup()

    def test_health_session_turn_search_and_model_error(self):
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertFalse(health.json()["model_configured"])
        created = self.client.post("/api/v1/sessions", json={"topic": "报告", "project_id": "p"})
        self.assertEqual(created.status_code, 201)
        session_id = created.json()["session_id"]
        turn = self.client.post(
            f"/api/v1/sessions/{session_id}/turns",
            json={
                "request_id": "r1",
                "events": [{"role": "user", "content": "这次报告使用中文。"}],
            },
        )
        self.assertEqual(turn.status_code, 201)
        search = self.client.post(
            "/api/v1/search",
            json={
                "session_id": session_id,
                "query": "这次报告使用什么语言",
                "top_k": 5,
            },
        )
        self.assertEqual(search.status_code, 200)
        self.assertLessEqual(search.json()["selected_k"], 5)
        self.assertTrue(search.json()["results"])
        answer = self.client.post(
            "/api/v1/answer",
            json={
                "session_id": session_id,
                "query": "这次报告使用什么语言",
            },
        )
        self.assertEqual(answer.status_code, 503)
        self.assertEqual(answer.json()["error"], "ModelNotConfigured")

    def test_unknown_session_is_not_an_empty_result(self):
        response = self.client.post(
            "/api/v1/search",
            json={
                "session_id": "missing",
                "query": "query",
            },
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
