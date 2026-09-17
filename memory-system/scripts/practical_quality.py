"""Real-provider conversational acceptance; original outputs and failures are retained."""

import asyncio
import json
import re
import tempfile
from pathlib import Path

from agent_memory.models import Event, Scope, TurnInput, utcnow
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


async def run():
    report = {
        "recorded_at": utcnow().isoformat(),
        "kind": "real_provider_synthetic_conversations",
        "cases": [],
    }
    with tempfile.TemporaryDirectory(prefix="memory-practical-") as folder:
        runtime = MemoryRuntime.from_settings(Settings(db_path=Path(folder) / "test.db", model_timeout=90))
        scope = Scope(tenant_id="practical", owner_id="synthetic")
        report["model"] = getattr(runtime.model, "model", None)
        report["embedding_enabled"] = runtime.retriever.embedder is not None
        counter = 0

        async def say(sid, content):
            nonlocal counter
            counter += 1
            await runtime.append_turn(
                scope,
                sid,
                TurnInput(request_id=f"input-{counter}", events=[Event(role="user", content=content)]),
            )

        async def ask(sid, name, query, expected=(), uncertain=False, forbidden=(), append=True):
            if append:
                await say(sid, query)
            row = {
                "name": name,
                "query": query,
                "expected": list(expected),
                "uncertain": uncertain,
                "forbidden": list(forbidden),
            }
            try:
                answer = await runtime.answer(scope, sid, query, timeout=180, accelerate=False)
                text = answer.generated_text
                checks = {
                    "expected": all(t.casefold() in text.casefold() for t in expected),
                    "forbidden": not any(t.casefold() in text.casefold() for t in forbidden),
                }
                if uncertain:
                    checks["unknown_acknowledged"] = bool(
                        re.search("不知道|不清楚|没有|未|无法|不确定|缺少|不明确", text)
                    )
                row.update(answer=answer.model_dump(mode="json"), checks=checks, passed=all(checks.values()))
                # Real front-end persists assistant replies; exercise contamination resistance.
                nonlocal counter
                counter += 1
                await runtime.append_turn(
                    scope,
                    sid,
                    TurnInput(request_id=f"reply-{counter}", events=[Event(role="assistant", content=text)]),
                )
            except Exception as exc:  # noqa: BLE001 - preserve every failed evaluation case
                row.update(passed=False, error={"type": type(exc).__name__, "detail": str(exc)[:400]})
            report["cases"].append(row)
            print(
                json.dumps(
                    {
                        "case": name,
                        "passed": row["passed"],
                        "text": row.get("answer", {}).get("generated_text", row.get("error")),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            Path("docs/PRACTICAL_QUALITY.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        try:
            s = runtime.store.create_session(scope, "昵称会话")
            await ask(s.session_id, "未知关系不猜测", "zfc是飞猪吗？", uncertain=True)
            await say(s.session_id, "zfc是飞猪！")
            await ask(
                s.session_id,
                "告知后直接回忆",
                "zfc是飞猪吗？",
                ("飞猪",),
                forbidden=("无法确认", "外部验证", "不能确认"),
            )
            await say(s.session_id, "更正：zfc的昵称不是飞猪，而是飞猫，之前我说错了。")
            await ask(
                s.session_id,
                "短期明确纠错",
                "zfc正确的昵称是什么？只说现在的昵称。",
                ("飞猫",),
                forbidden=("飞猪",),
            )
            fresh = runtime.store.create_session(scope, "独立会话")
            await ask(fresh.session_id, "普通会话事实不越界", "zfc是什么昵称？", uncertain=True)

            p = runtime.store.create_session(scope, "跨会话项目", "北辰")
            await say(
                p.session_id,
                "请长期记住以下北辰项目规则，仅用于北辰项目：阿岚的昵称是小舟；阿岚负责数据库迁移；项目默认使用PostgreSQL；报告默认用中文；项目验收地点是海棠会议室。",
            )
            await ask(
                p.session_id, "项目多事实写入", "项目的数据库和报告语言分别是什么？", ("PostgreSQL", "中文")
            )
            nextp = runtime.store.create_session(scope, "项目后续", "北辰")
            await ask(nextp.session_id, "跨会话直接召回", "北辰项目验收地点在哪里？", ("海棠",))
            await ask(nextp.session_id, "别名关联职责", "小舟负责什么工作？", ("数据库迁移",))
            await say(nextp.session_id, "仅本次报告用英文，北辰项目默认中文规则保持不变。")
            await ask(nextp.session_id, "临时覆盖长期默认", "这一次的报告应该用什么语言？", ("英文",))
            another = runtime.store.create_session(scope, "项目新任务", "北辰")
            await ask(
                another.session_id,
                "临时覆盖不污染其他会话",
                "报告默认用什么语言？",
                ("中文",),
                forbidden=("默认英文",),
            )
            other = runtime.store.create_session(scope, "异项目", "南辰")
            await ask(
                other.session_id,
                "项目隔离",
                "本项目默认数据库是什么？",
                uncertain=True,
                forbidden=("PostgreSQL",),
            )
            await say(
                another.session_id,
                "北辰项目验收地点由海棠会议室改为梧桐会议室，从现在开始生效，请更新长期记录。",
            )
            await ask(
                another.session_id,
                "长期现实变化",
                "现在验收地点在哪里？只回答当前地点。",
                ("梧桐",),
                forbidden=("海棠",),
            )
            reader = runtime.store.create_session(scope, "验证变更", "北辰")
            await ask(
                reader.session_id,
                "长期更新跨会话生效",
                "项目现在的验收地点是什么？",
                ("梧桐",),
                forbidden=("海棠",),
            )
            # Beyond the recent raw window; seed fact must survive extraction rather than raw prompt retention.
            long_s = runtime.store.create_session(scope, "长会话工作约束")
            await say(long_s.session_id, "这次发布的回滚口令是松鼠蓝莓，只用于当前会话。")
            for i in range(18):
                await say(long_s.session_id, f"第{i + 1}项检查完成，当前无需新增长期规则。")
            await ask(long_s.session_id, "长会话越过原文窗口", "这次发布回滚口令是什么？", ("松鼠蓝莓",))
            await say(long_s.session_id, "如果回滚口令是青柠会怎样？这只是一个假设，不改变实际口令。")
            await ask(
                long_s.session_id,
                "假设不覆盖事实",
                "实际回滚口令是什么？",
                ("松鼠蓝莓",),
                forbidden=("是青柠",),
            )
            report["maintenance"] = await runtime.long_term.maintain(scope)
            report["memory_inventory"] = [m.model_dump(mode="json") for m in runtime.store.memories(scope)]
        finally:
            await runtime.close()
    report["passed"] = sum(c["passed"] for c in report["cases"])
    report["total"] = len(report["cases"])
    Path("docs/PRACTICAL_QUALITY.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rows = [
        "# 实用会话真实模型评估",
        "",
        f"通过 {report['passed']}/{report['total']}；真实模型、合成会话、非公开基准。",
        "",
        "|案例|结果|回答/错误|",
        "|---|---|---|",
    ]
    for case in report["cases"]:
        answer = (
            case.get("answer", {})
            .get("generated_text", str(case.get("error", "")))
            .replace("\n", " ")
            .replace("|", "／")
        )
        rows.append(f"|{case['name']}|{'通过' if case['passed'] else '未通过'}|{answer}|")
    rows += [
        "",
        "判断器使用预先定义的关键词/拒猜/禁用词规则，保留逐题原始响应，不代表人工事实核验。",
        "完整输入、来源、查询轨迹及检查结果见 PRACTICAL_QUALITY.json。",
    ]
    Path("docs/PRACTICAL_QUALITY.md").write_text("\n".join(rows), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(run())
