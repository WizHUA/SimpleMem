"""Reproducible local evaluation. No credentials, network, or user's database.

Run from memory-system: python scripts/evaluate.py --output docs/EVALUATION.md
Synthetic fixtures test mechanics; they do not establish real-model QA quality.
"""

import argparse
import asyncio
import json
import math
import platform
import statistics
import tempfile
import time
import tracemalloc
from datetime import timedelta
from pathlib import Path

from agent_memory.models import Event, Evidence, Memory, QueryPlan, Scope, Session, TurnInput, utcnow
from agent_memory.retrieval import Retriever
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


def fact(identifier, text, **kwargs):
    values = {
        "memory_id": identifier,
        "session_id": "source",
        "scope_id": "owner",
        "tier": "long",
        "scope_type": "user",
        "subject": identifier,
        "predicate": "事实",
        "value": text,
        "content": text,
        "evidence": [Evidence(turn_id="fixture", event_index=0, quote=text)],
    }
    values.update(kwargs)
    return Memory(**values)


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


async def quality():
    now = utcnow()
    corpus = [
        fact("language", "用户偏好使用中文回复"),
        fact("deadline", "星河项目截止日期是9月30日"),
        fact("tool", "项目数据库使用 PostgreSQL"),
        fact("diet", "用户不吃花生，花生会引发过敏"),
        fact("old", "星河项目截止日期是9月10日", status="superseded", valid_to=now - timedelta(days=1)),
        fact("revoked", "用户偏好使用日语回复", status="retracted"),
        fact("pending", "星河项目截止日期是10月1日", status="pending"),
        fact("private", "火星项目秘密口令是红宝石", scope_type="project", scope_id="other-project"),
    ]
    cases = [
        ("语言偏好", "用户偏好使用什么语言回复", {"language"}),
        ("日期事实", "星河项目截止日期", {"deadline"}),
        ("技术事实", "项目数据库", {"tool"}),
        ("否定约束", "用户花生过敏", {"diet"}),
        ("同义改写挑战", "我的答复应采用哪种母语", {"language"}),
        ("英文改写挑战", "When is the Galaxy project due", {"deadline"}),
        ("未知问题", "月球天气预报", set()),
        ("项目隔离", "火星项目秘密口令", set()),
    ]
    rows, metrics = [], []
    for label, query, relevant in cases:
        result = await Retriever(Settings()).search(
            query, Session(session_id="s", project_id="p"), [], corpus
        )
        ids = [hit.metadata["memory_id"] for hit in result.results]
        hits = [index + 1 for index, mid in enumerate(ids) if mid in relevant]
        if relevant:
            recall = len(set(ids) & relevant) / len(relevant)
            rr = 1 / hits[0] if hits else 0
            dcg = sum(1 / math.log2(rank + 1) for rank in hits)
            ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(len(relevant), 3) + 1))
            metrics.append((int(bool(hits)), recall, rr, dcg / ideal))
        rows.append({"case": label, "query": query, "expected": sorted(relevant), "actual": ids})
        assert not {"revoked", "pending", "private", "old"} & set(ids), "eligibility regression"
    return {
        "positive_cases": len(metrics),
        "metrics": dict(
            zip(
                ["Hit@3", "Recall@3", "MRR@3", "NDCG@3"],
                [statistics.mean(column) for column in zip(*metrics)],
                strict=True,
            )
        ),
        "cases": rows,
    }


async def scale(repeats):
    rows = []
    session = Session(session_id="s")
    retriever = Retriever(Settings())
    for size in (100, 1000, 10000):
        corpus = [fact(f"m{i:06}", f"设备编号 X{i:06} 的校准周期为 {i % 30 + 1} 天") for i in range(size)]
        query = f"X{size - 1:06} 校准周期"
        await retriever.search(query, session, [], corpus)
        tracemalloc.start()
        timings = []
        for _ in range(repeats):
            started = time.perf_counter()
            result = await retriever.search(query, session, [], corpus)
            timings.append((time.perf_counter() - started) * 1000)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rows.append(
            {
                "memories": size,
                "runs": repeats,
                "p50_ms": statistics.median(timings),
                "p95_ms": percentile(timings, 0.95),
                "peak_mib": peak / 1024**2,
                "selected_k": result.selected_k,
            }
        )
    return rows


class MeasuredFixtureModel:
    """Fixed service-time test double; latency is NOT a real provider measurement."""

    def __init__(self):
        self.calls = 0

    async def plan(self, query, context):
        return QueryPlan()

    async def answer(self, prompt):
        self.calls += 1
        await asyncio.sleep(0.025)
        return "项目语言是中文【来源1】"


async def acceleration(repeats):
    with tempfile.TemporaryDirectory() as folder:
        model = MeasuredFixtureModel()
        runtime = MemoryRuntime(Settings(db_path=Path(folder) / "evaluation.db"), model=model)
        scope = Scope(tenant_id="evaluation", owner_id="synthetic")
        session = runtime.store.create_session(scope, "推理对照")
        write_times = []
        for i in range(40):
            started = time.perf_counter()
            await runtime.append_turn(
                scope,
                session.session_id,
                TurnInput(
                    request_id=f"turn-{i}",
                    events=[Event(role="user", content="项目语言是中文。" + "历史讨论。" * 20)],
                ),
            )
            write_times.append((time.perf_counter() - started) * 1000)
        rows, texts = [], []
        for name, enabled in (("baseline", False), ("accelerated", True)):
            before_calls = model.calls
            timings, avoided = [], 0
            for _ in range(repeats):
                response = await runtime.answer(scope, session.session_id, "项目语言", accelerate=enabled)
                texts.append(response.generated_text)
                timings.append(response.acceleration.total_ms)
                avoided += response.acceleration.avoided_model_calls
            rows.append(
                {
                    "mode": name,
                    "runs": repeats,
                    "p50_ms": statistics.median(timings),
                    "p95_ms": percentile(timings, 0.95),
                    "answer_model_calls": model.calls - before_calls,
                    "avoided_calls": avoided,
                    "context_before": response.acceleration.context_tokens_before,
                    "context_after": response.acceleration.context_tokens_after,
                }
            )
        assert len(set(texts)) == 1
        runtime.clear_scope_cache(scope)
        before_calls = model.calls
        burst = await asyncio.gather(
            *[runtime.answer(scope, session.session_id, "项目语言") for _ in range(8)]
        )
        concurrent = {
            "requests": 8,
            "answer_model_calls": model.calls - before_calls,
            "statuses": [r.acceleration.cache_status for r in burst],
        }
        await runtime.close()
    return {
        "fixture_model_delay_ms": 25,
        "rows": rows,
        "identical_answers": True,
        "concurrent": concurrent,
        "write_p50_ms": statistics.median(write_times),
        "write_p95_ms": percentile(write_times, 0.95),
    }


async def main(args):
    report = {
        "generated_at": utcnow().isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "quality": await quality(),
        "scale": await scale(args.repeats),
        "acceleration": await acceleration(args.repeats),
    }
    q = report["quality"]
    lines = [
        "# 可复现本地评估",
        "",
        f"运行时间：{report['generated_at']}。Python {report['python']}；{report['platform']}。",
        "",
        "所有数据均为独立临时库/合成样例，不读取用户数据库。质量集用于发现机制缺陷，不能代表真实用户问答正确率。",
        "",
        "## 检索质量",
        "",
        "配置：无模型规划、无 embedding，明确为 lexical_baseline；固定 top-3，正例6条，负例2条。",
        "",
        "|指标|值|",
        "|---|---:|",
    ]
    lines += [f"|{key}|{value:.3f}|" for key, value in q["metrics"].items()]
    lines += ["", "|用例|期望ID|实际ID|", "|---|---|---|"]
    lines += [
        f"|{row['case']}|{', '.join(row['expected']) or '无'}|{', '.join(row['actual']) or '无'}|"
        for row in q["cases"]
    ]
    lines += [
        "",
        "同义改写和英文改写是刻意保留的挑战，不为词法基线虚报语义能力；负例可能出现词法误召回。撤回、待确认、过期和其他项目的受限记录必须全部过滤。",
        "",
        "## 规模与延迟",
        "",
        "仅测试内存候选上的完整 Retriever（不含数据库读取、网络模型与嵌入），包含 tracemalloc 观测开销；不能当成 HTTP 吞吐或生产 SLA。",
        "",
        "|记忆数|重复次数|P50 ms|P95 ms|临时分配峰值 MiB|",
        "|---:|---:|---:|---:|---:|",
    ]
    lines += [
        f"|{r['memories']}|{r['runs']}|{r['p50_ms']:.2f}|{r['p95_ms']:.2f}|{r['peak_mib']:.2f}|"
        for r in report["scale"]
    ]
    a = report["acceleration"]
    lines += [
        "",
        "## 推理加速消融",
        "",
        "同一问题/证据/会话完整重复，规划固定，生成器是25 ms异步延迟的测试替身。对照数据包含冷启动一次；这里只验证机制与调用节省，真实模型延迟另行测量。",
        "",
        "|模式|次数|P50 ms|P95 ms|实际生成调用|避免生成调用|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    lines += [
        f"|{r['mode']}|{r['runs']}|{r['p50_ms']:.2f}|{r['p95_ms']:.2f}|{r['answer_model_calls']}|{r['avoided_calls']}|"
        for r in a["rows"]
    ]
    lines += [
        "",
        f"8个并发同上下文请求实测调用生成器 {a['concurrent']['answer_model_calls']} 次；两组回答文本一致。",
        f"40条写入 P50={a['write_p50_ms']:.2f} ms，P95={a['write_p95_ms']:.2f} ms。",
        f"上下文字符保守估计：完整历史 {a['rows'][-1]['context_before']} → 实际提示 {a['rows'][-1]['context_after']}，不是供应商计费 token。",
        "",
        "## 复现命令",
        "",
        "```powershell",
        "conda run -n simplemem-agentmemory python scripts/evaluate.py --repeats 10 --output docs/EVALUATION.md",
        "```",
        "",
        "机器、并发、候选数、追踪开销都会改变延迟。真实宿主、真实模型回答正确率/忠实度/引用F1、长时间负载仍需独立验收，不由这些合成结果替代。",
        "",
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    args.output.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--output", type=Path, default=Path("docs/EVALUATION.md"))
    options = parser.parse_args()
    if options.repeats < 2:
        parser.error("--repeats must be >= 2")
    asyncio.run(main(options))
