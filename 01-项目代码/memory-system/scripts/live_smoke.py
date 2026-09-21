"""Explicit, small real-provider smoke test using synthetic data and a temporary DB."""

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from agent_memory.models import Event, Scope, TurnInput, utcnow
from agent_memory.ports import ModelRequestError
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


async def main(output):
    with tempfile.TemporaryDirectory() as folder:
        runtime = MemoryRuntime.from_settings(
            Settings(
                db_path=Path(folder) / "live-smoke.db", model_timeout=90, inference_cache_ttl_seconds=300
            )
        )
        try:
            if runtime.model is None:
                raise RuntimeError("No model configured; this is not a simulated smoke test")
            scope = Scope(tenant_id="live-smoke", owner_id="synthetic")
            first = runtime.store.create_session(scope, "跨会话长期记忆测试", "星河")
            await runtime.append_turn(
                scope,
                first.session_id,
                TurnInput(
                    request_id="seed",
                    events=[
                        Event(
                            role="user",
                            content="请将以下内容存入长期记忆：我的回答偏好是中文；星河项目默认使用 PostgreSQL 数据库。",
                        )
                    ],
                ),
            )
            extracted = await runtime.short_term.extract(scope, first.session_id)
            durable = runtime.store.memories(scope, tier="long")
            second = runtime.store.create_session(scope, "跨会话回忆验证", "星河")
            rows = []
            for label, enabled in (("baseline", False), ("cold", True), ("warm", True)):
                result = await runtime.answer(
                    scope, second.session_id, "我长期的回答偏好是什么？", timeout=90, accelerate=enabled
                )
                rows.append(
                    {
                        "mode": label,
                        "answer": result.generated_text,
                        "citations": result.citations,
                        "source_count": result.retrieval_count,
                        "acceleration": result.acceleration.model_dump(),
                        "warnings": result.warnings,
                    }
                )
            report = {
                "generated_at": utcnow().isoformat(),
                "provider": getattr(runtime.model, "provider", "host"),
                "model": getattr(runtime.model, "model", "host"),
                "data": "synthetic temporary database",
                "extraction_status": extracted["status"],
                "durable_count": len(durable),
                "durable_contents": [m.content for m in durable],
                "measurements": rows,
                "checks": {
                    "durable_memory_created": bool(durable),
                    "cross_session_recalled": all(row["source_count"] > 0 for row in rows),
                    "chinese_preference_answered": all("中文" in row["answer"] for row in rows),
                    "warm_cache_hit": rows[-1]["acceleration"]["cache_hit"],
                },
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.with_suffix(".json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            lines = [
                "# 真实模型冒烟记录",
                "",
                f"时间：{report['generated_at']}；提供商：{report['provider']}；模型：{report['model']}。",
                "",
                "使用独立临时数据库与合成消息；调用实际已配置服务。仅一次小样本，不是统计性性能或质量结论。未输出密钥、服务配置或用户数据。",
                "",
                f"抽取状态：{report['extraction_status']}；长期记忆数：{len(durable)}。",
                "",
                "|模式|总耗时ms|检索含规划ms|生成/复用ms|缓存|避免调用|",
                "|---|---:|---:|---:|---|---:|",
            ]
            for row in rows:
                trace = row["acceleration"]
                lines.append(
                    f"|{row['mode']}|{trace['total_ms']:.1f}|{trace['retrieval_ms']:.1f}|{trace['generation_ms']:.1f}|{trace['cache_status']}|{trace['avoided_model_calls']}|"
                )
            lines += ["", "## 检查结果", ""] + [
                f"- {key}: {value}" for key, value in report["checks"].items()
            ]
            lines += ["", "## 实际回答", ""] + [f"**{row['mode']}**：{row['answer']}" for row in rows]
            lines += [
                "",
                "检索规划每次重新执行，缓存只减少最终生成调用。样本只核对中文偏好及有来源，不能替代人工事实核验、引用忠实度评价或真实宿主验收。",
                "",
            ]
            output.write_text("\n".join(lines), encoding="utf-8")
            print(json.dumps({"output": str(output), "checks": report["checks"]}, ensure_ascii=False))
            if not all(report["checks"].values()):
                raise RuntimeError("One or more live smoke checks failed; inspect the report")
        except ModelRequestError as error:
            output.parent.mkdir(parents=True, exist_ok=True)
            report = {
                "generated_at": utcnow().isoformat(),
                "status": "blocked",
                "provider_http_status": error.status_code,
                "stage": "real_provider_smoke",
                "quality_verified": False,
            }
            output.with_suffix(".json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            output.write_text(
                "# 真实模型冒烟记录\n\n"
                f"时间：{report['generated_at']}。实际请求配置的模型服务，返回 HTTP {error.status_code}。\n\n"
                "状态：**未通过，外部服务阻塞**。仅使用合成消息和独立临时数据库，没有修改用户数据。\n\n"
                "HTTP 402 表示付费/额度相关错误；请在账户侧处理，或在私有后端 .env 更换可用服务。"
                "本次没有真实模型回答质量或加速耗时结果，不能用合成替身数据替代。\n\n"
                "配置恢复后执行：\n\n```powershell\n"
                "conda run --no-capture-output -n simplemem-agentmemory python scripts/live_smoke.py\n```\n",
                encoding="utf-8",
            )
            raise SystemExit(f"Live provider smoke blocked: HTTP {error.status_code}; see {output}") from None
        finally:
            await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/LIVE_SMOKE.md"))
    args = parser.parse_args()
    asyncio.run(main(args.output))
