"""Bounded offline ASGI load experiment using disposable SQLite databases."""

import argparse
import asyncio
import json
import math
import platform
import statistics
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

from agent_memory.api import create_app
from agent_memory.runtime import MemoryRuntime
from agent_memory.settings import Settings


def summarize(samples, elapsed):
    latencies = sorted(sample["ms"] for sample in samples)
    codes = Counter(sample["status"] for sample in samples)
    failures = sum(count for code, count in codes.items() if code != 200 and code != 201)
    return {
        "requests": len(samples),
        "p50_ms": statistics.median(latencies),
        "p95_ms": latencies[math.ceil(0.95 * len(latencies)) - 1],
        "failure_rate": failures / len(samples),
        "status_counts": dict(codes),
        "throughput_per_second": len(samples) / elapsed,
        "raw_ms": latencies,
    }


async def batch(client, concurrency, requests):
    gate = asyncio.Semaphore(concurrency)

    async def one(path, payload):
        async with gate:
            started = time.perf_counter()
            try:
                response = await client.post(path, json=payload)
                status = response.status_code
            except httpx.HTTPError:
                status = 0
            return {"status": status, "ms": (time.perf_counter() - started) * 1000}

    started = time.perf_counter()
    samples = await asyncio.gather(*(one(path, payload) for path, payload in requests))
    return summarize(samples, time.perf_counter() - started)


async def measure(root, concurrency, rounds):
    settings = Settings(
        db_path=root / f"http-{concurrency}.sqlite3",
        api_key="",
        deployment_mode="local",
        local_tenant="load-test",
        local_owner="offline",
    )
    runtime = MemoryRuntime(settings, model=None)
    app = create_app(settings, runtime)
    ids = []
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://load.test") as client,
        ):
            for _ in range(concurrency * rounds):
                response = await client.post("/api/v1/sessions", json={"topic": "HTTP load"})
                response.raise_for_status()
                ids.append(response.json()["session_id"])
            # Warm HTTP transport/threadpool without contaminating measured sessions.
            warm = await client.post("/api/v1/sessions", json={"topic": "warmup"})
            warm.raise_for_status()
            warm_id = warm.json()["session_id"]
            warmup = await client.post(
                f"/api/v1/sessions/{warm_id}/turns",
                json={
                    "request_id": "warmup",
                    "events": [{"role": "user", "content": "warmup"}],
                },
            )
            warmup.raise_for_status()
            writes = await batch(
                client,
                concurrency,
                [
                    (
                        f"/api/v1/sessions/{sid}/turns",
                        {
                            "request_id": f"write-{index}",
                            "events": [{"role": "user", "content": "项目报告语言固定使用中文。"}],
                        },
                    )
                    for index, sid in enumerate(ids)
                ],
            )
            queries = await batch(
                client,
                concurrency,
                [
                    ("/api/v1/search", {"session_id": sid, "query": "当前会话报告语言", "timeout": 30})
                    for sid in ids
                ],
            )
    finally:
        await runtime.close()

    # Reopen the actual file with a fresh runtime and verify committed turns through HTTP.
    restored = MemoryRuntime(settings, model=None)
    restored_app = create_app(settings, restored)
    durable = 0
    try:
        async with (
            restored_app.router.lifespan_context(restored_app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=restored_app), base_url="http://load.test"
            ) as client,
        ):
            for sid in ids:
                response = await client.get(f"/api/v1/sessions/{sid}")
                if response.status_code == 200:
                    turns = response.json()["recent_turns"]
                    durable += int(len(turns) == 1 and turns[0]["sequence"] == 1)
    finally:
        await restored.close()
    return {
        "concurrency": concurrency,
        "writes": writes,
        "queries": queries,
        "persisted_sessions": durable,
        "expected_sessions": len(ids),
    }


async def run(args):
    with tempfile.TemporaryDirectory(prefix="agent-memory-http-load-") as folder:
        results = [await measure(Path(folder), concurrency, args.rounds) for concurrency in args.concurrency]
    return {
        "recorded_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "rounds": args.rounds,
        "results": results,
    }


def report(data):
    lines = [
        "# HTTP 边界与 SQLite 持久化负载验证",
        "",
        f"执行时间 `{data['recorded_at']}`；Python {data['python']}；{data['platform']}。",
        "",
        "使用真实 FastAPI 路由、Pydantic 校验、异步运行时、线程卸载和临时 SQLite 文件。",
        "HTTPX ASGITransport 在同一进程内调用服务，没有经过 Uvicorn、TCP、TLS、反向代理或外网。",
        "这不是网络 SLA，也不包含真实 LLM 或 embedding 性能。未读取用户数据库，未调用模型。",
        "",
        "本实验每个请求使用独立会话，写入阶段结束后执行查询。",
        "它测量共享 SQLite 上的并发持久化与稳定快照查询，不代表同一会话写入/查询竞争。",
        "生产中的并发状态变化可能正确返回 409，另由 HTTP 回归测试覆盖。",
        "",
        "| 并发上限 | 操作 | 请求数 | P50 ms | P95 ms | 失败率 | 请求/秒 |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for item in data["results"]:
        for field, label in [("writes", "轮次持久化"), ("queries", "原文检索")]:
            metric = item[field]
            lines.append(
                f"| {item['concurrency']} | {label} | {metric['requests']} | "
                f"{metric['p50_ms']:.2f} | {metric['p95_ms']:.2f} | "
                f"{metric['failure_rate']:.2%} | {metric['throughput_per_second']:.2f} |"
            )
    lines.extend(["", "关闭原运行时并重新打开 SQLite 后，经 HTTP 检查持久化轮次：", ""])
    for item in data["results"]:
        lines.append(
            f"- 并发 {item['concurrency']}：{item['persisted_sessions']}/{item['expected_sessions']} "
            "个会话恰好保存一轮，sequence=1。"
        )
    lines.extend(
        [
            "",
            "延迟从获得并发槽后开始测量，包含服务中的等待；不含客户端等待并发槽时间。",
            "P95 采用排序样本的最近秩定义；失败率包含非 200/201 状态与 HTTP 客户端错误。",
            "独立会话均只有一轮，不能外推大型单会话或大型长期记忆库检索。",
            "",
            "## 复现",
            "",
            "```powershell",
            "conda run -n simplemem-agentmemory python scripts/http_load.py --concurrency 30 100 --rounds 3",
            "conda run -n simplemem-agentmemory python -m pytest tests/test_http_delivery.py -q",
            "```",
            "",
            "HTTP 交付测试包含写入幂等、抽取来源、跨会话查询、回答缓存、事实替代与撤回、",
            "查询/生成期间撤回返回 409，以及 bearer 鉴权和项目范围隔离。",
            "抽取与回答使用确定性模型夹具，不可作为真实模型质量实验。",
            "",
            "## 原始测量",
            "",
            "以下延迟样本已排序，用于复核分位数。",
            "",
            "```json",
            json.dumps(data, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", nargs="+", type=int, default=[30, 100])
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("docs/HTTP_LOAD.md"))
    args = parser.parse_args()
    if not 1 <= args.rounds <= 20 or any(not 1 <= value <= 500 for value in args.concurrency):
        parser.error("rounds must be 1–20; concurrency must be 1–500")
    data = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report(data), encoding="utf-8")
    print(f"Wrote {args.output}")
    return int(
        any(
            item["persisted_sessions"] != item["expected_sessions"]
            or item["writes"]["failure_rate"]
            or item["queries"]["failure_rate"]
            for item in data["results"]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
