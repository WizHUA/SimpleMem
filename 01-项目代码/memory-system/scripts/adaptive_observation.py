"""Real-provider adaptive retrieval and fixed-context cache observations."""

import asyncio
import json
import tempfile
from pathlib import Path

from agent_memory.models import Event, Scope, TurnInput, utcnow
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


async def run():
    output = Path(__file__).resolve().parents[1] / "docs" / "reports"
    output.mkdir(parents=True, exist_ok=True)
    report = {"recorded_at": utcnow().isoformat(), "kind": "real_provider_synthetic_adaptive_observation"}
    with tempfile.TemporaryDirectory(prefix="adaptive-observation-") as folder:
        runtime = MemoryRuntime.from_settings(Settings(db_path=Path(folder) / "memory.db", model_timeout=90))
        scope = Scope(tenant_id="adaptive-observation", owner_id="synthetic")
        session = runtime.store.create_session(scope, "合成项目记忆观察").session_id
        report["model"] = getattr(runtime.model, "model", None)
        report["embedding_enabled"] = runtime.retriever.embedder is not None
        facts = [
            "项目星舟的负责人是林然。",
            "项目星舟的交付日期是2026年12月18日。",
            "项目星舟的报告使用中文。",
        ]
        report["facts"] = facts
        try:
            for index, fact in enumerate(facts):
                await runtime.append_turn(
                    scope,
                    session,
                    TurnInput(request_id=f"fact-{index}", events=[Event(role="user", content=fact)]),
                )
            query = "项目星舟的负责人、交付日期和报告语言分别是什么？"
            report["adaptive_query"] = query
            answer = await runtime.answer(scope, session, query, timeout=180, accelerate=False)
            report["adaptive_answer"] = answer.model_dump(mode="json")
            report["checks"] = {
                "adaptive_plan_reported": answer.dynamic_k is not None,
                "all_facts_answered": all(
                    item in answer.generated_text for item in ("林然", "12", "18", "中文")
                ),
                "unconfigured_semantic_not_claimed": runtime.retriever.embedder is not None
                or all(c.status == "disabled" for c in answer.channels if c.view == "semantic"),
            }
            # Prepare once; comparison never appends messages or changes memory.
            comparison = "请用一行列出项目星舟的负责人及交付日期。"
            report["comparison_query"] = comparison
            report["comparison"] = []
            for label, accelerate in (("baseline", False), ("cold", True), ("warm", True)):
                result = await runtime.answer(
                    scope, session, comparison, timeout=180, accelerate=accelerate, prepare=False
                )
                report["comparison"].append({"label": label, "answer": result.model_dump(mode="json")})
                print(
                    json.dumps(
                        {
                            "phase": label,
                            "status": result.acceleration.cache_status,
                            "elapsed_ms": result.elapsed_ms,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            report["checks"]["baseline_does_not_cache"] = (
                report["comparison"][0]["answer"]["acceleration"]["cache_status"] == "disabled"
            )
            report["checks"]["cold_miss"] = (
                report["comparison"][1]["answer"]["acceleration"]["cache_status"] == "miss"
            )
            report["checks"]["warm_hit"] = (
                report["comparison"][2]["answer"]["acceleration"]["cache_status"] == "hit"
            )
        except Exception as exc:  # noqa: BLE001 - record failures without provider body or credentials
            report["error_type"] = type(exc).__name__
        finally:
            await runtime.close()
    (output / "ADAPTIVE_OBSERVATION.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 动态检索与回答复用真实观察",
        "",
        f"模型：{report['model']}；Embedding 启用：{report['embedding_enabled']}。临时合成数据库，未修改用户数据。",
        "",
        "首个请求省略 top_k，记录计划与真实通道；后续比较采用固定数据库和会话上下文，不追加助手消息，先禁用缓存基线，再启用缓存冷、热各一次。",
        "",
    ]
    adaptive = report.get("adaptive_answer", {})
    lines += [
        f"问题：{report.get('adaptive_query', '')}",
        "",
        f"原始回答：{adaptive.get('generated_text', report.get('error_type', ''))}",
        "",
        "```json",
        json.dumps(adaptive.get("dynamic_k"), ensure_ascii=False, indent=2),
        "```",
        "",
        "|层|通道|状态|输入|命中|最终入选|",
        "|---|---|---|---:|---:|---:|",
    ]
    for channel in adaptive.get("channels", []):
        lines.append(
            f"|{channel['tier']}|{channel['view']}|{channel['status']}|{channel['input_count']}|{channel['matched_count']}|{channel['selected_count']}|"
        )
    lines += ["", "|请求|缓存状态|检索 ms|生成 ms|总计 ms|避免模型调用|", "|---|---|---:|---:|---:|---:|"]
    for row in report.get("comparison", []):
        trace = row["answer"]["acceleration"]
        lines.append(
            f"|{row['label']}|{trace['cache_status']}|{trace['retrieval_ms']:.1f}|{trace['generation_ms']:.1f}|{trace['total_ms']:.1f}|{trace['avoided_model_calls']}|"
        )
    lines += [
        "",
        f"检查：`{json.dumps(report.get('checks', {}), ensure_ascii=False)}`",
        "",
        "该样本只验证实际路径和观测数据，不代表统计性能倍数。缓存仍重新执行规划与检索；模型规划若改变生成上下文，热请求可能继续 miss，报告保留实际结果。长度预算为保守字符估计，通道分数不是事实置信度。完整响应见同名 JSON。",
    ]
    (output / "ADAPTIVE_OBSERVATION.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(run())
