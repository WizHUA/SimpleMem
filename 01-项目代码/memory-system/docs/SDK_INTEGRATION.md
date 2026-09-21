# 接入课程 Engine SDK 1.0

`agent_memory` 核心不依赖宿主。只有 `agent_memory.integrations.rag_sdk` 导入真实 `server.engines.memory_plugin_api`。仓库中的测试 shim 只存在于 pytest 隔离模块，不能复制到生产环境冒充 SDK。

## 安装与注册

在宿主使用的 Python 3.11+ 环境安装 `memory-system`，将以下配置合入宿主 `config.json`，保留原有键：

```json
{
  "engines": {"modules": ["agent_memory.integrations.rag_sdk"], "allow_override": false},
  "retrieval": {
    "engine_weights": {"simple_memory": 0.5},
    "engine_timeouts": {"simple_memory": 30.0}
  }
}
```

也可在 `server/engines/simple_memory/__init__.py` 中导出 `from agent_memory.integrations.rag_sdk import engine_plugin`，二选一，避免重复注册。不要直接导入 `agent_memory.integrations`，其包入口刻意不加载可选 SDK。

宿主启动时创建一个运行时，停机时关闭。初始化存储可能执行同步 IO，异步 lifespan 使用 `asyncio.to_thread`：

```python
import asyncio
from agent_memory.runtime import MemoryRuntime
from agent_memory.integrations.rag_sdk import bind_request, configure, engine_plugin

runtime = await asyncio.to_thread(MemoryRuntime.from_settings)
configure(runtime)
try:
    # verified_scope 必须来自已认证主体，authorized_session_id 必须已通过会话授权。
    with bind_request(verified_scope, authorized_session_id):
        results = await engine_plugin.search(query, top_k=10, timeout=30)
finally:
    await runtime.close()  # 实际放在宿主 lifespan 的退出路径，仅关闭一次。
```

SDK 原始接口没有 tenant/owner/session 参数，因此上下文绑定是必需的宿主改动。必须在创建检索子任务之前绑定，ContextVar 才能正确传入子任务。不要直接信任前端 Header 或 query 中的 owner_id。未绑定时检索/生成抛出 `PermissionError`；嵌套绑定和异常退出自动恢复；公开上下文副本不能改变其他并发任务的身份。

## 流与错误

```python
with bind_request(verified_scope, authorized_session_id):
    async for event, payload in engine_plugin.generate_stream(query, timeout=30):
        yield encode_host_sse(event, payload)  # 复用宿主 JSON 单行编码。
```

绑定必须覆盖异步生成器的**迭代阶段**，仅覆盖生成器对象创建无效。当前流为缓冲兼容：完整回答后发送一个 `engine_token`，不是模型逐 token 流。事件只有 start/status/token/done 四种；正常完成或普通错误各有一个 done，错误不泄漏后端异常文本。连接取消传播 `CancelledError`，不向已关闭流追加伪造 done，宿主需要清理该请求的聚合计数。

`search/generate` 异常向上传播，交给宿主熔断；超时限制实际 await。`top_k` 为 0–100 整数、query 为非空且最多 3000 字符、timeout 必须有限且正值。探活每次调用重新读取 health，不永久缓存；模型缺失不影响基础检索可用性，生成请求仍明确失败。底层 `to_thread` 中已开始的同步工作不能强制终止，取消语义不应表述为杀死所有工作线程。

## 写入与能力声明

插件声明 search/generate/stream，关闭 ingest/delete/browse；宿主文件构建流程与对话记忆写入不是同一语义，不认领 `.pdf` 或 `.txt`。会话、轮次、抽取、演化通过已授权运行时或服务 API 完成。保留调用方稳定 request_id，实现重试幂等。SDK 返回 source_file 使用 `[memory:...]` 可辨识标记和真实 chunk_id，分数范围为 0–1。词法基线分数不冒充向量余弦相似度。

## 分层验收

1. 独立回归：`python -m pytest tests/test_sdk_bridge.py -q`。这是测试 shim 契约测试，覆盖并发隔离、嵌套、取消、超时、参数边界、唯一终止事件和单行 JSON。
2. 真实契约：`python scripts/validate_host_sdk.py --host-root D:\path\to\host`。必须存在真实 `server/engines/memory_plugin_api.py`；缺少时退出 2 并明确 blocked，不生成替代 SDK。
3. 真实注册：宿主重启后确认 `GET /api/v2/memory-engine/engines` 中出现 simple_memory；若走目录注册，运行课程提供的 `validate_engine.py simple_memory`。
4. 真实调用：完成 startup configure 与认证绑定后，运行课程 `smoke_test_engine.py simple_memory`，以及带身份的搜索、生成、SSE、两个用户交叉查询、超时和取消用例。官方 smoke 未携带本项目身份上下文时，需宿主集成方补上认证链，不能用默认共享用户绕过隔离。

当前资料包只包含 SDK 说明、模板和验证脚本，不含实际宿主实现或运行实例。第 2–4 层须在集成环境验收，不能由第 1 层替代。
