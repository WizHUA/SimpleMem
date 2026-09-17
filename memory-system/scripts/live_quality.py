"""Small real-provider quality probe; lexical rules and raw answers, no model judge."""

import argparse
import asyncio
import json
import re
import tempfile
from pathlib import Path

from agent_memory.models import Event, EvolutionInput, Scope, TurnInput, utcnow
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings

SEED = (
    "请把以下四条已确认事实作为项目级长期记忆保存，仅适用于星桥项目，不是全局用户偏好："
    "星桥项目报告的默认输出语言是中文；"
    "星桥项目报告截止日期是2026年11月20日；"
    "星桥项目默认数据库是PostgreSQL；"
    "星桥项目报告导出格式是PDF。"
)


def normalized(text):
    return re.sub(r"\s+", "", text.lower()).replace("年", "-").replace("月", "-").replace("日", "")


def error_data(error):
    return {"type": type(error).__name__, "upstream_status": getattr(error, "status_code", None)}


async def ask(
    runtime, scope, sid, name, query, *, expected=(), forbidden=(), empty_sources=False, refusal=False
):
    row = {
        "name": name,
        "query": query,
        "expected": list(expected),
        "forbidden": list(forbidden),
        "require_empty_sources": empty_sources,
        "require_refusal_marker": refusal,
    }
    try:
        answer = await runtime.answer(scope, sid, query, timeout=90, accelerate=False)
        text = normalized(answer.generated_text)
        checks = {
            "expected_terms": all(normalized(term) in text for term in expected),
            "forbidden_terms_absent": all(normalized(term) not in text for term in forbidden),
        }
        if expected:
            checks["retrieved_source_present"] = bool(answer.sources)
            checks["valid_citation_present"] = bool(answer.citations)
        if empty_sources:
            checks["sources_empty"] = not answer.sources
        if refusal:
            checks["refusal_marker"] = bool(re.search(r"未|没有|无法|不清楚|不足|未知|不确定|缺少", text))
        row.update(
            {
                "status": "completed",
                "checks": checks,
                "passed": all(checks.values()),
                "answer": answer.generated_text,
                "elapsed_ms": answer.elapsed_ms,
                "citations": answer.citations,
                "sources": [hit.model_dump(mode="json") for hit in answer.sources],
                "warnings": answer.warnings,
                "retrieval_mode": answer.retrieval_mode,
                "plan": answer.plan.model_dump(mode="json") if answer.plan else None,
            }
        )
    except Exception as error:  # noqa: BLE001 - preserve per-case provider and validation failures
        row.update({"status": "error", "passed": False, "error": error_data(error)})
    return row


async def run():
    report = {
        "recorded_at": utcnow().isoformat(),
        "seed": SEED,
        "planned_questions": 10,
        "cases": [],
        "stages": [],
    }
    with tempfile.TemporaryDirectory(prefix="agent-memory-live-quality-") as folder:
        runtime = MemoryRuntime.from_settings(
            Settings(
                db_path=Path(folder) / "quality.sqlite3",
                model_timeout=90,
                api_key="",
                deployment_mode="local",
            )
        )
        try:
            report.update(
                {
                    "provider": getattr(runtime.model, "provider", "unknown"),
                    "model": getattr(runtime.model, "model", "unknown"),
                    "embedding_configured": runtime.retriever.embedder is not None,
                }
            )
            if runtime.model is None:
                raise RuntimeError("Real model is not configured")
            scope = Scope(tenant_id="live-quality", owner_id="synthetic-alice")
            first = runtime.store.create_session(scope, "原始四事实", "星桥")
            await runtime.append_turn(
                scope,
                first.session_id,
                TurnInput(
                    request_id="seed",
                    events=[Event(role="user", content=SEED)],
                ),
            )
            extracted = await runtime.short_term.extract(scope, first.session_id)
            memories = runtime.store.memories(scope, first.session_id)
            # Promotion is an explicit engineering action, never described as automatic model judgment.
            promoted = []
            for memory in memories:
                if memory.tier == "short" and memory.durable and memory.status == "active":
                    await runtime.long_term.evolve(
                        scope,
                        memory.memory_id,
                        EvolutionInput(
                            action="promote",
                            expected_version=memory.version,
                        ),
                    )
                    promoted.append(memory.memory_id)
            report["stages"].append(
                {
                    "stage": "initial_extraction",
                    "status": extracted["status"],
                    "explicitly_promoted": promoted,
                }
            )
            initial = runtime.store.memories(scope, tier="long")
            report["initial_memories"] = [memory.model_dump(mode="json") for memory in initial]
            second = runtime.store.create_session(scope, "同项目新会话", "星桥")
            other_project = runtime.store.create_session(scope, "其他项目", "云帆")
            other_scope = Scope(tenant_id="live-quality", owner_id="synthetic-bob")
            other_owner = runtime.store.create_session(other_scope, "不同用户", "星桥")
            cases = [
                (
                    scope,
                    second.session_id,
                    "语言直接查询",
                    "星桥项目报告默认使用什么语言？",
                    {"expected": ["中文"]},
                ),
                (
                    scope,
                    second.session_id,
                    "日期回忆",
                    "星桥项目报告的截止日期是哪一天？",
                    {"expected": ["2026-11-20"]},
                ),
                (
                    scope,
                    second.session_id,
                    "数据库回忆",
                    "星桥项目默认使用什么数据库？",
                    {"expected": ["PostgreSQL"]},
                ),
                (
                    scope,
                    second.session_id,
                    "中文改写",
                    "给星桥团队写报告时，文字应该采用哪种语言？",
                    {"expected": ["中文"]},
                ),
                (
                    scope,
                    second.session_id,
                    "多事实组合",
                    "请同时列出星桥项目报告截止日期、默认数据库和导出格式。",
                    {"expected": ["2026-11-20", "PostgreSQL", "PDF"]},
                ),
                (
                    scope,
                    second.session_id,
                    "未知预算拒猜",
                    "星桥项目预算具体是多少元？没有记录就明确说明。",
                    {"refusal": True},
                ),
                (
                    scope,
                    other_project.session_id,
                    "项目隔离",
                    "云帆项目默认使用什么数据库？没有记录就明确说明。",
                    {"forbidden": ["PostgreSQL"], "empty_sources": True, "refusal": True},
                ),
                (
                    other_scope,
                    other_owner.session_id,
                    "用户隔离",
                    "星桥项目默认使用什么数据库？没有记录就明确说明。",
                    {"forbidden": ["PostgreSQL"], "empty_sources": True, "refusal": True},
                ),
            ]
            for identity, sid, name, question, rules in cases:
                row = await ask(runtime, identity, sid, name, question, **rules)
                report["cases"].append(row)
                print(json.dumps({"case": name, "passed": row["passed"]}, ensure_ascii=False), flush=True)

            original = [m for m in initial if "2026-11-20" in normalized(m.content + m.value)]
            correction = "请记录正式更正：星桥项目报告截止日期由2026年11月20日改为2026年12月5日，以12月5日为准。仅适用于星桥项目。"
            await runtime.append_turn(
                scope,
                first.session_id,
                TurnInput(
                    request_id="correction",
                    events=[Event(role="user", content=correction)],
                ),
            )
            updated = await runtime.short_term.extract(scope, first.session_id)
            current = runtime.store.memories(scope, first.session_id)
            candidates = [
                m
                for m in current
                if "2026-12-5" in normalized(m.content + m.value)
                or "2026-12-05" in normalized(m.content + m.value)
            ]
            matches = [
                (old, new)
                for old in original
                for new in candidates
                if old.subject == new.subject
                and old.predicate == new.predicate
                and old.memory_id != new.memory_id
            ]
            stage = {
                "stage": "correction",
                "input": correction,
                "extraction_status": updated["status"],
                "candidates": [m.model_dump(mode="json") for m in candidates],
            }
            if len(matches) != 1:
                stage.update(
                    {
                        "status": "failed",
                        "reason": "Expected one exact subject/predicate match",
                        "matches": len(matches),
                    }
                )
            else:
                old, new = matches[0]
                result = await runtime.long_term.evolve(
                    scope,
                    new.memory_id,
                    EvolutionInput(
                        action="supersede",
                        expected_version=new.version,
                        target_id=old.memory_id,
                        target_version=old.version,
                        effective_at=utcnow(),
                    ),
                )
                stage.update(
                    {
                        "status": "superseded",
                        "explicit_action": "store evolution after real extraction",
                        "memory": result.model_dump(mode="json"),
                    }
                )
            report["stages"].append(stage)
            for name, question in [
                ("更正后回忆", "星桥项目报告的当前截止日期是什么？"),
                ("更正后中文改写", "星桥报告现在最晚应当在哪一天交付？"),
            ]:
                # Both accepted zero-padding spellings are canonicalized below for exact lexical checks.
                row = await ask(runtime, scope, second.session_id, name, question, forbidden=["2026-11-20"])
                if row["status"] == "completed":
                    answer_text = normalized(row["answer"])
                    row["checks"]["corrected_date"] = any(
                        date in answer_text for date in ["2026-12-5", "2026-12-05"]
                    )
                    row["checks"]["retrieved_source_present"] = bool(row["sources"])
                    row["checks"]["valid_citation_present"] = bool(row["citations"])
                    row["passed"] = all(row["checks"].values())
                report["cases"].append(row)
                print(json.dumps({"case": name, "passed": row["passed"]}, ensure_ascii=False), flush=True)
        except Exception as error:  # noqa: BLE001 - retain partial results without leaking provider payloads
            report["fatal_error"] = error_data(error)
        finally:
            await runtime.close()
    return report


def save(report, output):
    rows = report["cases"]
    completed = sum(row["status"] == "completed" for row in rows)
    passed = sum(row["passed"] for row in rows)
    report["summary"] = {
        "planned": 10,
        "attempted": len(rows),
        "completed": completed,
        "lexical_rule_passed": passed,
        "errors": len(rows) - completed,
    }
    lines = [
        "# 真实模型合成小集检查",
        "",
        f"执行时间：{report['recorded_at']}。",
        f"提供商：{report.get('provider')}；模型：{report.get('model')}；embedding配置：{report.get('embedding_configured')}。",
        "",
        "真实模型调用、临时 SQLite、合成四事实。全程关闭回答缓存，问题顺序固定，每题只执行一次。",
        "本实验采用词面规则，不是公开语义质量基准，也没有使用模型评委。",
        "引用仅检查存在合法编号，不等同引用忠实度。拒猜仅检查否定/缺少信息措辞，不足以证明答案没有幻觉。",
        "",
        f"计划10题，实际尝试{len(rows)}题，完成{completed}题，请求错误{len(rows) - completed}题；",
        f"词面与来源规则通过{passed}/{len(rows)}题（未执行题不计入该分母，仍单列）。",
        "",
        "## 数据与维护流程",
        "",
        SEED,
        "",
        "四条事实一次真实抽取。若模型返回可持久化的短期候选，由脚本显式提升，并记录ID。",
        "更正使用第二次真实抽取；仅在主体/谓词精确匹配且候选唯一时，脚本显式执行 supersede。",
        "没有自动修补抽取字段或更改规则来追求通过率。维护动作不是模型自动关系判断。",
        "",
    ]
    for stage in report["stages"]:
        lines += [f"阶段 `{stage['stage']}`：`{stage.get('status', stage.get('extraction_status'))}`。", ""]
        if stage.get("reason"):
            lines += [f"维护未执行原因：{stage['reason']}；匹配数 {stage.get('matches')}。", ""]
        for memory in stage.get("candidates", []):
            lines += [
                (
                    f"更正候选主体 `{memory['subject']}`，谓词 `{memory['predicate']}`，"
                    f"值 `{memory['value']}`，生效时间 `{memory['valid_from']}`。"
                ),
                "",
            ]
    lines += [
        "更正查询是整条维护流程的检查。如果精确关系匹配失败，旧记忆仍有效，后续失败不能归因于已执行的 supersede。",
        "更正规则禁止回答出现旧日期，属于保守词面约束；正确的历史对比也可能被它判错，需查看原始回答。",
        "",
        "## 逐题原始结果",
        "",
    ]
    for index, row in enumerate(rows, 1):
        lines += [
            f"### {index}. {row['name']}",
            "",
            f"问题：{row['query']}",
            "",
            f"状态：{row['status']}；规则通过：{row['passed']}。",
            "",
            row.get("answer", "请求失败，见原始记录。"),
            "",
            f"规则检查：`{json.dumps(row.get('checks', row.get('error')), ensure_ascii=False)}`",
            "",
            f"来源：`{json.dumps([s['content'] for s in row.get('sources', [])], ensure_ascii=False)}`",
            "",
        ]
    lines += [
        "## 复现与限制",
        "",
        "```powershell",
        "conda run --no-capture-output -n simplemem-agentmemory python scripts/live_quality.py",
        "```",
        "",
        "重跑会产生模型服务费用。答案具有随机性，失败记录保留，不以重试择优。",
        "未测试真实宿主、公开长期对话集或真实用户数据；10题不支持统计推断或模型排名。",
        "完整抽取内容、显式维护动作、来源与检查规则见同名 JSON。",
        "",
    ]
    if report.get("fatal_error"):
        lines += [f"运行中断：`{json.dumps(report['fatal_error'])}`", ""]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/LIVE_QUALITY.md"))
    args = parser.parse_args()
    report = asyncio.run(run())
    save(report, args.output)
    return int(
        bool(report.get("fatal_error"))
        or len(report["cases"]) != 10
        or not all(row["passed"] for row in report["cases"])
    )


if __name__ == "__main__":
    raise SystemExit(main())
