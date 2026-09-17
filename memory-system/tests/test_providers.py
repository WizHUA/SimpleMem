"""Provider contracts exercise mocks only; tests never need keys or a model server."""

import asyncio
import json
import os
import threading
import unittest
from unittest.mock import patch

import httpx

from agent_memory.models import Event, ExtractionWindow, Session, Turn
from agent_memory.ports import ModelNotConfigured
from agent_memory.providers import (
    CallableModel,
    ModelOutputError,
    OpenAICompatibleModel,
    UnconfiguredModel,
    build_model,
)
from agent_memory.settings import Settings


def sample_window() -> ExtractionWindow:
    return ExtractionWindow(
        session=Session(session_id="session1"),
        new_turns=[
            Turn(
                turn_id="turn1",
                session_id="session1",
                sequence=1,
                request_id="request1",
                events=[Event(role="user", content="这次请用中文回答。")],
            )
        ],
        context_turns=[],
    )


def sample_extraction() -> dict:
    return {
        "candidates": [
            {
                "content": "当前任务使用中文回答",
                "subject": "当前任务",
                "predicate": "回答语言",
                "value": "中文",
                "scope_type": "session",
                "durable": False,
                "evidence": [{"turn_id": "turn1", "event_index": 0, "quote": "这次请用中文回答。"}],
            }
        ],
        "summary": "当前任务使用中文",
        "state_patch": {},
    }


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_has_no_factual_fallback(self):
        model = UnconfiguredModel()
        for operation in (
            lambda: model.extract(sample_window()),
            lambda: model.plan("query", {}),
            lambda: model.answer("prompt"),
        ):
            with self.assertRaises(ModelNotConfigured):
                await operation()

    async def test_valid_extraction_and_fenced_json(self):
        prompts = []

        async def chat(prompt):
            prompts.append(prompt)
            return "```json\n" + json.dumps(sample_extraction(), ensure_ascii=False) + "\n```"

        result = await CallableModel(chat).extract(sample_window())
        self.assertEqual(result.candidates[0].value, "中文")
        self.assertEqual(len(prompts), 1)
        self.assertIn("Event.occurred_at", prompts[0])
        self.assertIn("context_turns", prompts[0])

    async def test_format_repair_is_bounded_and_strict(self):
        calls = []

        async def chat(prompt):
            calls.append(prompt)
            return '{"route":"short","depth":"3"}' if len(calls) == 1 else '{"route":"short","depth":3}'

        result = await CallableModel(chat).plan("刚才要求什么", {})
        self.assertEqual(result.depth, 3)
        self.assertEqual(len(calls), 2)
        self.assertIn("previous_response", calls[1])

    async def test_invalid_format_stops_after_two_calls(self):
        calls = 0

        async def chat(prompt):
            nonlocal calls
            calls += 1
            return '{"route":"short","owner_id":"other-owner"}'

        with self.assertRaises(ModelOutputError):
            await CallableModel(chat).plan("query", {})
        self.assertEqual(calls, 2)

    async def test_schema_repair_keeps_original_deadline(self):
        calls = 0

        async def chat(prompt):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.08)
            return "bad JSON" if calls == 1 else '{"route":"short","depth":3}'

        with self.assertRaises(TimeoutError):
            await CallableModel(chat, timeout=0.12).plan("query", {})
        self.assertEqual(calls, 2)

    async def test_network_or_host_error_is_not_repaired(self):
        calls = 0

        async def chat(prompt):
            nonlocal calls
            calls += 1
            raise ConnectionError("host unavailable")

        with self.assertRaises(ConnectionError):
            await CallableModel(chat).plan("query", {})
        self.assertEqual(calls, 1)

    async def test_sync_host_callable_runs_in_worker_thread(self):
        main_thread = threading.get_ident()
        seen_threads = []

        def chat(prompt):
            seen_threads.append(threading.get_ident())
            return "answer"

        self.assertEqual(await CallableModel(chat).answer("prompt"), "answer")
        self.assertNotEqual(seen_threads[0], main_thread)

    async def test_answer_has_no_json_repair(self):
        calls = 0

        async def chat(prompt):
            nonlocal calls
            calls += 1
            return "依据 [1]，当前使用中文。"

        self.assertEqual(await CallableModel(chat).answer("prompt"), "依据 [1]，当前使用中文。")
        self.assertEqual(calls, 1)

    async def test_new_only_evidence_cannot_reference_context(self):
        data = sample_extraction()
        data["candidates"][0]["evidence"][0]["turn_id"] = "old-turn"

        async def chat(prompt):
            return json.dumps(data)

        with self.assertRaisesRegex(ModelOutputError, "new turn"):
            await CallableModel(chat).extract(sample_window())

    async def test_evidence_must_be_exact_quote(self):
        data = sample_extraction()
        data["candidates"][0]["evidence"][0]["quote"] = "以后所有任务都用中文"

        async def chat(prompt):
            return json.dumps(data)

        with self.assertRaisesRegex(ModelOutputError, "exact substring"):
            await CallableModel(chat).extract(sample_window())

    async def test_assistant_alone_cannot_be_stated_fact_source(self):
        window = sample_window()
        window.new_turns[0].events[0].role = "assistant"

        async def chat(prompt):
            return json.dumps(sample_extraction())

        with self.assertRaisesRegex(ModelOutputError, "Assistant"):
            await CallableModel(chat).extract(window)

    async def test_assistant_plan_is_marked_and_kept_provisional(self):
        window = sample_window()
        window.new_turns[0].events[0].role = "assistant"
        data = sample_extraction()
        data["candidates"][0]["assertion"] = "planned"

        async def chat(prompt):
            return json.dumps(data)

        result = await CallableModel(chat).extract(window)
        self.assertEqual(result.candidates[0].assertion, "planned")
        self.assertFalse(result.candidates[0].durable)

    async def test_context_can_support_new_reference_resolution(self):
        window = sample_window()
        window.context_turns.append(
            Turn(
                turn_id="context1",
                session_id="session1",
                sequence=0,
                request_id="context-request",
                events=[Event(role="user", content="这个任务是项目 P 的汇报。")],
            )
        )
        data = sample_extraction()
        data["candidates"][0]["evidence"].append(
            {
                "turn_id": "context1",
                "event_index": 0,
                "quote": "项目 P 的汇报",
            }
        )

        async def chat(prompt):
            return json.dumps(data)

        result = await CallableModel(chat).extract(window)
        self.assertEqual(len(result.candidates[0].evidence), 2)

    async def test_planner_does_not_receive_whole_store_gold_or_identity(self):
        seen = []

        async def chat(prompt):
            seen.append(prompt)
            return '{"route":"none","depth":1}'

        await CallableModel(chat).plan(
            "1+1",
            {
                "summary": "当前任务",
                "owner_id": "SECRET_OWNER",
                "gold_answer": "SECRET_GOLD",
                "memories": ["SECRET_MEMORY"],
            },
        )
        self.assertIn("当前任务", seen[0])
        for marker in ("SECRET_OWNER", "SECRET_GOLD", "SECRET_MEMORY"):
            self.assertNotIn(marker, seen[0])

    async def test_mock_http_endpoint_preserves_v1_and_model_configuration(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            model = OpenAICompatibleModel(
                base_url="https://example.invalid/v1/",
                model="configured-model",
                api_key="mock-key",
                client=client,
            )
            self.assertEqual(await model.answer("prompt"), "hello")
            await model.aclose()
            self.assertFalse(client.is_closed, "Injected clients remain owned by the caller")
        self.assertEqual(str(requests[0].url), "https://example.invalid/v1/chat/completions")
        self.assertEqual(requests[0].headers["Authorization"], "Bearer mock-key")
        self.assertEqual(json.loads(requests[0].content)["model"], "configured-model")
        self.assertEqual(json.loads(requests[0].content)["max_tokens"], 4096)

    async def test_mock_http_status_error_propagates_without_retry(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(503, text="unavailable")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            model = OpenAICompatibleModel(base_url="https://example.invalid", model="test", client=client)
            with self.assertRaises(httpx.HTTPStatusError):
                await model.plan("query", {})
        self.assertEqual(len(requests), 1)

    async def test_factory_unconfigured_partial_and_local_endpoint(self):
        empty = {"MEMORY_MODEL_BASE_URL": "", "MEMORY_MODEL_NAME": "", "MEMORY_MODEL_API_KEY": ""}
        with patch.dict(os.environ, empty):
            self.assertIsNone(build_model(Settings()))
        with (
            patch.dict(os.environ, empty | {"MEMORY_MODEL_API_KEY": "mock-key"}),
            self.assertRaises(ValueError),
        ):
            build_model(Settings())
        with patch.dict(
            os.environ,
            empty
            | {
                "MEMORY_MODEL_BASE_URL": "http://localhost:1234/v1",
                "MEMORY_MODEL_NAME": "local",
            },
        ):
            model = build_model(Settings())
            self.assertIsInstance(model, OpenAICompatibleModel)
            await model.aclose()

    async def test_process_environment_overrides_dotenv_configuration(self):
        configured = {
            "MEMORY_MODEL_BASE_URL": "https://example.invalid/v4",
            "MEMORY_MODEL_NAME": "configured-model",
            "MEMORY_MODEL_API_KEY": "local-secret",
        }
        with patch.dict(os.environ, configured):
            model = build_model(Settings())
            self.assertEqual(model.endpoint, "https://example.invalid/v4/chat/completions")
            self.assertEqual(model.model, "configured-model")
            await model.aclose()


if __name__ == "__main__":
    unittest.main()
