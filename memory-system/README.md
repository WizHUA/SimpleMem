# Agent Memory Service

这是一个独立的长短期记忆后端骨架

当前目标是给后续开发提供稳定边界：

- 前后端分离：后端提供 `/api/v1` JSON API 和 `/docs` OpenAPI 页面；未来前端只依赖这些接口。
- 方法解耦：SimpleMem 提供短期压缩和意图查询思路；Zep 提供有效期和历史版本规则；H-MEM 当前仅提供长期分组视图。
- 宿主可嵌入：核心包不导入 `server.*`；真实 RAG SDK 通过 `agent_memory.integrations.rag_sdk` 接入。
- 失败明确：没有配置模型时，原始消息仍可查询；抽取和回答返回 503，不生成伪记忆或伪答案。

## 当前已实现

```text
HTTP / RAG SDK
       |
MemoryRuntime
  |-- ShortTermMemory: 待处理 5 轮 + 最多 15 轮语境，严格来源校验
  |-- Retriever: SimpleMem 式意图规划、STM 优先、三视图、动态 K
  |-- LongTermMemory: 显式 promote/merge/supersede/retract/defer
  `-- SQLiteStore: 会话、轮次、记忆版本、审计和 H-MEM 引用分组
```

当前检索是单用户数据范围内的小规模扫描。没有 Embedder 时会明确返回 `lexical_baseline`，不会把词法分数称为向量语义分数。后续接入宿主 Qdrant 后，应用服务应只取授权 owner/scope 的候选，再复用相同最终筛选逻辑。

## 安装与运行

```powershell
cd "C:\Users\zhangjinhan\Desktop\Agent Mem\memory-system"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m agent_memory
```

本地地址：`http://127.0.0.1:8088`，接口文档：`http://127.0.0.1:8088/docs`。

默认身份固定为环境变量中的本地 principal。不要把服务绑定到公网；生产嵌入时必须替换 `api.get_scope`，从宿主认证结果创建 `Scope`。任意客户端 Header 不是认证。

模型是可选依赖。项目启动时会读取根目录 `.env`，已有进程环境变量优先。使用智谱 GLM 时配置：

```powershell
$env:MEMORY_MODEL_BASE_URL="https://open.bigmodel.cn/api/paas/v4"
$env:MEMORY_MODEL_NAME="glm-5.3"
$env:MEMORY_MODEL_API_KEY="<仅保存在后端的密钥>"
$env:MEMORY_MODEL_TIMEOUT="90"
$env:MEMORY_MODEL_MAX_TOKENS="4096"
python -m agent_memory
```

也可以把 `.env.example` 复制为 `.env` 后填写相同三项。`.env` 已被 Git 忽略；不要把密钥写进前端、源码、Markdown 或日志。

未配置模型也可以创建会话、记录轮次、查询近期原文和检查健康状态。

## API 使用顺序

```text
POST /api/v1/sessions
POST /api/v1/sessions/{id}/turns
POST /api/v1/sessions/{id}/extract       配置模型后调用
POST /api/v1/search
POST /api/v1/answer                       配置模型后调用
GET  /api/v1/memories
POST /api/v1/memories/{id}/evolve         后台/管理面显式动作
GET  /api/v1/memories/{id}/history
POST /api/v1/maintenance
GET  /api/v1/groups
```

`turns` 要求稳定 `request_id`，相同内容重试返回同一个 turn；相同 ID 不同内容返回 409。`extract` 失败不推进水位。`evolve` 要求 `expected_version`，避免旧任务覆盖新状态。

调用方应先把用户 Query 作为 turn 保存，再调用 search/answer；回答和工具结果随后使用新的 `request_id` 追加。后端不会在 `answer` 内隐式写回，以免重试时重复记录。`turns` 响应的 `extraction_due/extraction_reasons` 同时反映五轮上限和 token 压力；阶段结束、明确纠正等语义事件由调用方显式调用 `extract`。

## SDK 接入

核心运行时可以独立开发。真实宿主中才导入：

```python
from agent_memory.integrations.rag_sdk import bind_request, configure
from agent_memory.runtime import MemoryRuntime

runtime = MemoryRuntime.from_settings()
configure(runtime)

# 宿主认证和会话授权之后，在创建检索子任务前绑定：
with bind_request(verified_scope, authorized_session_id):
    results = await engine_plugin.search(query, top_k=10, timeout=30)
```

SDK 插件支持 `search/generate/generate_stream`，写入仍走本服务 API，因此 `ingest/delete/browse` 首版关闭。`generate_stream` 是缓冲兼容模式，完整回答生成后发送一个 token 事件，并非模型逐 token 流。

当前工作区没有真实宿主 `memory_plugin_api.py`，测试使用隔离 shim 验证接口形状，不能替代正式的 `validate_plugin` 和 HTTP 冒烟。

## 测试

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python -m pytest -q
python -m ruff check src tests
```

测试不访问真实模型、网络或 Qdrant。

## 下一步

1. 将 `api.get_scope` 替换为宿主身份依赖，并增加 session 授权。
2. 用代表性中文开发集校准窗口、语义阈值和动态 K；参数仍集中在 `Settings`。
3. 接入宿主 `LLMInterface` 的 `CallableModel`，先验证抽取 Schema 与来源准确率。
4. 增加 Qdrant/Embedding 候选适配器；关系状态和历史仍由权威库判断。
5. 实现候选关系分类器，只输出建议动作，继续由 `SQLiteStore.evolve` 做版本检查。
6. 在长期数据规模足够后，再将 `reference_group` 升级为 H-MEM 层级摘要；查询主线仍保持 SimpleMem 意图规划和动态 K。
7. 最后接入真实 RAG SDK，运行官方 validation、HTTP 冒烟和报告中的组件/端到端实验。

详细模块和协作边界见 [ARCHITECTURE.md](ARCHITECTURE.md)，开发任务见 [DEVELOPMENT.md](DEVELOPMENT.md)。
实现流程和所有大模型调用点见 [IMPLEMENTATION_FRAMEWORK.md](IMPLEMENTATION_FRAMEWORK.md)。

独立验证前端位于相邻的 [`../memory-ui`](../memory-ui)。前端只调用 HTTP API，不读取 SQLite，也不保存模型密钥。
