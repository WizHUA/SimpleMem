"""Small real-provider UX evaluation in an isolated synthetic database."""

import asyncio
import json
import re
import tempfile
from pathlib import Path

from agent_memory.models import Event, Scope, TurnInput, utcnow
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


async def run():
    out = Path(__file__).resolve().parents[1] / "docs" / "reports"
    out.mkdir(parents=True, exist_ok=True)
    report = {"recorded_at": utcnow().isoformat(), "kind": "real_provider_synthetic_ux", "cases": []}
    with tempfile.TemporaryDirectory(prefix="answer-ux-") as folder:
        runtime = MemoryRuntime.from_settings(Settings(db_path=Path(folder) / "memory.db", model_timeout=90))
        scope = Scope(tenant_id="ux-evaluation", owner_id="synthetic")
        report["model"] = getattr(runtime.model, "model", None)
        report["embedding_enabled"] = runtime.retriever.embedder is not None
        session = runtime.store.create_session(scope, "回答与来源交互验收").session_id
        serial = 0

        async def say(content, answer_id=None):
            nonlocal serial
            serial += 1
            return await runtime.append_turn(
                scope,
                session,
                TurnInput(
                    request_id=f"ux-{serial}",
                    events=[
                        Event(role="assistant" if answer_id else "user", content=content, answer_id=answer_id)
                    ],
                ),
            )

        async def ask(name, query, required=(), forbidden=(), unknown=False, provenance=False):
            await say(query)
            row = {"name": name, "query": query, "required": required, "forbidden": forbidden}
            try:
                answer = await runtime.answer(scope, session, query, timeout=180, accelerate=False)
                text = answer.generated_text
                checks = {
                    "required": all(s in text for s in required),
                    "forbidden": not any(s in text for s in forbidden),
                    "receipt": bool(answer.answer_id),
                }
                if unknown:
                    checks["admits_unknown"] = bool(re.search("不知道|不清楚|没有|未|无法|不确定|缺少", text))
                if provenance:
                    checks["explains_requested_source"] = bool(
                        re.search("告诉|提到|说|记录|来源|告知|提供", text)
                    )
                saved = await say(text, answer.answer_id)
                context = saved["turn"].events[0].answer_context
                checks["snapshot_bound"] = context is not None and context.sources == answer.sources
                row.update(answer=answer.model_dump(mode="json"), checks=checks, passed=all(checks.values()))
            except Exception as exc:  # noqa: BLE001 - retain failures without provider response or secrets
                row.update(passed=False, error_type=type(exc).__name__)
            report["cases"].append(row)
            (out / "ANSWER_UX_QUALITY.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps({"case": name, "passed": row["passed"]}, ensure_ascii=False), flush=True)

        try:
            await ask("空白会话的zfc不幻觉", "zfc是飞猪吗？", unknown=True)
            session = runtime.store.create_session(scope, "昵称与更正独立会话").session_id
            await ask("未知昵称不猜测", "mrx的昵称是什么？", unknown=True)
            await say("zfc的昵称是飞猪。")
            no_asides = ("按你之前", "用户说", "你的设定", "外部验证", "无法确认")
            await ask("昵称直接回答", "zfc是飞猪吗？", required=("飞猪",), forbidden=no_asides)
            await say("更正：zfc现在的昵称是飞猫，不是飞猪，之前说错了。")
            await ask(
                "当前值更正", "zfc现在的昵称是什么？", required=("飞猫",), forbidden=(*no_asides, "飞猪")
            )
            await ask(
                "主动询问来源", "你怎么知道zfc叫飞猫？告诉我依据。", required=("飞猫",), provenance=True
            )
        finally:
            await runtime.close()
    report.update(passed=sum(c["passed"] for c in report["cases"]), total=len(report["cases"]))
    (out / "ANSWER_UX_QUALITY.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 回答与来源交互真实模型验收",
        "",
        f"规则检查通过 {report['passed']}/{report['total']}。模型：{report['model']}。",
        "",
        "使用临时合成数据库和真实模型；不修改用户记忆。提示词仅包含通用风格指导，没有具体昵称关系示例。首先在空白会话询问 zfc，随后在同主体的另一会话告知昵称和更正。小样本关键词规则不能替代人工可用性评估。每次回答实际保存 receipt 并校验来源快照。",
        "",
        "|案例|结果|原始回答|",
        "|---|---|---|",
    ]
    for case in report["cases"]:
        answer = case.get("answer", {}).get("generated_text", case.get("error_type", ""))
        lines.append(
            f"|{case['name']}|{'通过' if case['passed'] else '失败'}|{answer.replace(chr(10), '<br>').replace('|', '／')}|"
        )
    lines += [
        "",
        "详细来源、检索计划、原始回答和各项判定见 ANSWER_UX_QUALITY.json。首轮包含具体昵称关系提示，已单独保留在 ANSWER_UX_QUALITY_INITIAL.*，不能作为该关系的独立验收。本轮为删除该提示后的首次复测，没有筛选最佳输出。",
    ]
    (out / "ANSWER_UX_QUALITY.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(run())
