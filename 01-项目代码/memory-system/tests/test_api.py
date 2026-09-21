"""HTTP boundary checks using the fixed local-development principal."""

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from agent_memory.api import create_app
from agent_memory.ports import ModelRequestError
from agent_memory.providers import CallableModel
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
        self.runtime.retriever.close()
        self.runtime.store.close()
        self.folder.cleanup()

    def test_health_session_turn_search_and_model_error(self):
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertFalse(health.json()["model_configured"])
        self.assertIsNone(health.json()["model_name"])
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

    def test_model_auth_failure_is_reported_without_leaking_provider_response(self):
        class UnauthorizedModel:
            async def plan(self, query, context):
                request = httpx.Request("POST", "https://example.invalid/chat/completions")
                response = httpx.Response(401, text="private upstream details", request=request)
                raise httpx.HTTPStatusError("Unauthorized", request=request, response=response)

        self.runtime.model = UnauthorizedModel()
        self.runtime.retriever.model = self.runtime.model
        created = self.client.post("/api/v1/sessions", json={})
        session_id = created.json()["session_id"]
        response = self.client.post(
            "/api/v1/answer",
            json={"session_id": session_id, "query": "你好"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("MEMORY_MODEL_API_KEY", response.json()["detail"])
        self.assertNotIn("private upstream details", response.text)

    def test_provider_auth_error_uses_safe_deepseek_message(self):
        class UnauthorizedModel:
            async def plan(self, query, context):
                raise ModelRequestError(401)

        self.runtime.model = UnauthorizedModel()
        self.runtime.retriever.model = self.runtime.model
        session_id = self.client.post("/api/v1/sessions", json={}).json()["session_id"]
        response = self.client.post("/api/v1/answer", json={"session_id": session_id, "query": "你好"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("MEMORY_MODEL_API_KEY", response.json()["detail"])

    def test_provider_payment_required_is_safe_service_unavailable(self):
        class PaymentRequiredModel:
            async def plan(self, query, context):
                raise ModelRequestError(402)

        self.runtime.model = PaymentRequiredModel()
        self.runtime.retriever.model = self.runtime.model
        session_id = self.client.post("/api/v1/sessions", json={}).json()["session_id"]
        response = self.client.post("/api/v1/answer", json={"session_id": session_id, "query": "你好"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "模型服务额度不足或需要付费，请检查账户与计费配置。")

    def test_chat_round_trip_appends_generated_reply(self):
        def chat(prompt):
            if "短期记忆的增量生成" in prompt:
                return '{"candidates":[],"summary":"这次报告使用中文"}'
            if "用户意图感知查询规划" in prompt:
                return '{"route":"short","semantic_queries":["报告语言"],"depth":1}'
            return "这次报告使用中文。"

        model = CallableModel(chat)
        self.runtime.model = model
        self.runtime.short_term.model = model
        self.runtime.retriever.model = model
        session_id = self.client.post("/api/v1/sessions", json={}).json()["session_id"]
        for role, content in (
            ("user", "这次报告使用中文。"),
            ("user", "这次报告使用什么语言？"),
        ):
            response = self.client.post(
                f"/api/v1/sessions/{session_id}/turns",
                json={"request_id": str(uuid4()), "events": [{"role": role, "content": content}]},
            )
            self.assertEqual(response.status_code, 201)
        answer = self.client.post(
            "/api/v1/answer",
            json={"session_id": session_id, "query": "这次报告使用什么语言？"},
        )
        self.assertEqual(answer.status_code, 200)
        generated = answer.json()["generated_text"]
        self.assertEqual(generated, "这次报告使用中文。")
        appended = self.client.post(
            f"/api/v1/sessions/{session_id}/turns",
            json={"request_id": str(uuid4()), "events": [{"role": "assistant", "content": generated}]},
        )
        self.assertEqual(appended.status_code, 201)
        state = self.client.get(f"/api/v1/sessions/{session_id}").json()
        self.assertEqual(state["recent_turns"][-1]["events"][0]["content"], generated)

    def test_action_plan_answer_uses_expected_markdown_shape(self):
        def chat(prompt):
            if "用户意图感知查询规划" in prompt:
                return (
                    '{"route":"both","response_intent":"action_plan",'
                    '"semantic_queries":["蓝港演练方案"],'
                    '"problem_breakdown":["明确目标和边界","盘点资源与协同"],'
                    '"required_info":["行动目标","兵力编成与位置","任务指令卡"],'
                    '"information_gathering":["从短期记忆读取本轮目标和资源状态"],'
                    '"integration_steps":["先确定目标，再形成任务卡"],'
                    '"action_template":"ops_plan_task_cards",'
                    '"action_elements":["方案名称","作战概述","兵力部署","任务指令卡"],'
                    '"depth":10}'
                )
            self.assertIn("# 【方案名称】", prompt)
            self.assertIn("## 作战概述", prompt)
            self.assertIn("## 情景假设与分案", prompt)
            self.assertIn("| 单位 | 平台类型 | 位置 | 任务角色 |", prompt)
            self.assertIn("- **交战规则/约束**:", prompt)
            self.assertIn("不要把整份方案大量写成“待补充”", prompt)
            self.assertIn("不得虚构兵力", prompt)
            self.assertIn("需确认", prompt)
            return (
                "# 蓝港演练方案\n\n"
                "## 作战概述\n\n"
                "本方案基于已知演练目标组织，未知地形不阻塞生成，按情景假设分案处理。\n\n"
                "## 兵力部署\n\n"
                "作战区域：未指定，按情景假设推演\n"
                "中心坐标：未指定\n"
                "作战边界：依据任务方向和情景假设设置原则性边界\n"
                "兵力编成与位置：\n"
                "| 单位 | 平台类型 | 位置 | 任务角色 |\n"
                "|---|---|---|---|\n"
                "| 记录组 | 抽象资源类别 | 需确认 | 态势记录 |\n"
                "| 检索组 | 抽象资源类别 | 需确认 | 信息检索 |\n"
                "部署阵型：按山地/城镇/海岛/开阔地分案调整\n"
                "总体概述：记录组、检索组按任务链路协同，缺少位置需确认。\n\n"
                "## 情景假设与分案\n"
                "- 山地/丘陵：优先建立观察与通信接力。\n"
                "- 海岛/滨海：优先考虑机动与补给受限。\n\n"
                "---\n\n"
                "## 任务指令卡\n\n"
                "---\n\n"
                "### 记录组 - 态势记录\n"
                "- **任务**: 建立态势记录并同步检索组\n"
                "- **任务类型**: 协同演练\n"
                "- **任务目标**: 保持方案事实和假设可追踪\n"
                "- **时间要求**: 需确认\n"
                "- **装备清单**: 只使用已知抽象资源\n"
                "- **目标分配**: 按信息类型分配，不指定真实目标坐标\n"
                "- **协同关系**: 与检索组双向校核\n"
                "- **交战规则/约束**: 不虚构兵力装备；未知关键事实列入需确认\n"
                "- **执行要点**:\n"
                "  - 若地形为山地，则优先补充观察点与通信链路假设\n"
            )

        model = CallableModel(chat)
        self.runtime.model = model
        self.runtime.short_term.model = model
        self.runtime.retriever.model = model
        session_id = self.client.post("/api/v1/sessions", json={}).json()["session_id"]
        answer = self.client.post(
            "/api/v1/answer",
            json={"session_id": session_id, "query": "请生成蓝港演练的完整方案与任务指令卡"},
        )
        self.assertEqual(answer.status_code, 200)
        generated = answer.json()["generated_text"]
        self.assertIn("## 作战概述", generated)
        self.assertIn("## 兵力部署", generated)
        self.assertIn("## 情景假设与分案", generated)
        self.assertIn("| 单位 | 平台类型 | 位置 | 任务角色 |", generated)
        self.assertIn("## 任务指令卡", generated)
        self.assertIn("- **交战规则/约束**:", generated)
        self.assertIn("需确认", generated)

    def test_hierarchy_is_derived_from_backend_and_starts_empty(self):
        response = self.client.get("/api/v1/hierarchy")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["domain_count"], 0)
        self.assertEqual(response.json()["domains"], [])


if __name__ == "__main__":
    unittest.main()
