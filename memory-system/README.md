# Agent Memory Service

独立的长短期记忆后端与课程 Engine 适配器，配套相邻 `memory-ui` 展示前端。开发交付分支：`full-forward`。

交付入口见 [DELIVERY.md](../DELIVERY.md)。包含来源可追溯的短期抽取、跨会话长期记忆、版本与有效期、可选真实 embedding、精确上下文推理复用、并发安全和身份边界。当前定位为单节点交付候选版本，真实课程宿主与目标部署环境仍需验收。

- [课程需求矩阵](docs/COURSE_REQUIREMENTS.md) · [验收矩阵](docs/ACCEPTANCE.md)
- [技术报告](docs/技术报告.md) · [20分钟答辩与演示脚本](docs/答辩演示脚本.md)
- [可复现评测](docs/EVALUATION.md) · [真实模型冒烟](docs/LIVE_SMOKE.md) · [真实模型质量小集](docs/LIVE_QUALITY.md) · [HTTP并发与持久化](docs/HTTP_LOAD.md)
- [SDK接入](docs/SDK_INTEGRATION.md) · [部署、备份与数据治理](OPERATIONS.md)

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
# 从 SimpleMem 目录执行；使用仓库指定的 Conda 环境，不创建 .venv。
cd memory-system
conda run --no-capture-output -n simplemem-agentmemory python -m pip install -e ".[dev]"
conda run --no-capture-output -n simplemem-agentmemory python -m agent_memory
```

本地地址：`http://127.0.0.1:8088`，接口文档：`http://127.0.0.1:8088/docs`。

默认身份固定为环境变量中的本地 principal，开发服务绑定回环地址。独立部署可使用 `MEMORY_DEPLOYMENT_MODE=production` 与至少32字符的 `MEMORY_API_KEY`，数据接口验证 Bearer。多用户嵌入必须替换 `api.get_scope`，从宿主认证结果创建 `Scope`；共用服务密钥不是多用户身份系统。

模型是可选依赖。项目启动时会读取 `memory-system/.env`，已有进程环境变量优先。使用 DeepSeek 官方 Chat Completions API 时配置：

```powershell
$env:MEMORY_MODEL_BASE_URL="https://api.deepseek.com"
$env:MEMORY_MODEL_NAME="deepseek-flash"
$env:MEMORY_MODEL_API_KEY="<仅保存在后端的密钥>"
$env:MEMORY_MODEL_TIMEOUT="90"
$env:MEMORY_MODEL_MAX_TOKENS="4096"
python -m agent_memory
```

也可以在已有 `.env` 中填写相同配置。DeepSeek Key 留空时后端仍可启动，但 `/health` 显示 `model_configured=false`，回答与抽取暂不可用。DeepSeek 调用会关闭默认思考模式；查询规划和记忆抽取使用 JSON 输出，回答使用普通文本。已有 GLM 配置仍受支持，两者使用相同的 `MEMORY_MODEL_*` 变量，一次运行只选择一个模型。修改 `.env` 后需重启后端，`GET /health` 可确认实际模型名和提供商。`.env` 已被 Git 忽略；不要把密钥写进前端、源码、Markdown 或日志。

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

## 加速与语义检索

`POST /api/v1/answer` 接受 `accelerate: true/false`，返回 `acceleration`：新鲜检索耗时、生成或复用耗时、完整上下文与实际上下文的字符估计、缓存状态及避免的生成调用数。每次先做授权和检索；完整提示、来源版本、会话状态与主体一致时，才复用生成结果。TTL/LRU限制缓存，重复并发合并为一次生成，最后等待者取消时终止共享调用。记忆修改、删除和有效期跨界会阻止旧快照返回。

前端“运行观测”可做不增加对话事件的基线/加速重跑。正常聊天每轮修改状态，通常不会命中；不要将重复问题命中率宣称为所有任务加速收益。计数是生成调用节省，规划仍重新执行；字符估计不是供应商计费 token。

真实向量服务使用独立的 `MEMORY_EMBEDDING_BASE_URL/NAME/API_KEY`。未配置则明确返回 `lexical_baseline`；配置后使用批量向量、余弦召回及有界嵌入缓存。当前仍扫描授权候选，不是分布式向量索引。

## 后续部署验收

1. 将 `api.get_scope` 替换为宿主身份依赖，并增加 session 授权。
2. 用代表性中文开发集校准窗口、语义阈值和动态 K；参数仍集中在 `Settings`。
3. 接入宿主 `LLMInterface` 的 `CallableModel`，先验证抽取 Schema 与来源准确率。
4. 大规模部署接入 Qdrant 等候选索引；已提供真实 Embedding 接口，关系状态和历史仍由权威库判断。
5. 实现候选关系分类器，只输出建议动作，继续由 `SQLiteStore.evolve` 做版本检查。
6. 在长期数据规模足够后，再将 `reference_group` 升级为 H-MEM 层级摘要；查询主线仍保持 SimpleMem 意图规划和动态 K。
7. 最后接入真实 RAG SDK，运行官方 validation、HTTP 冒烟和报告中的组件/端到端实验。

详细模块和协作边界见 [ARCHITECTURE.md](ARCHITECTURE.md)，开发任务见 [DEVELOPMENT.md](DEVELOPMENT.md)。
实现流程和所有大模型调用点见 [IMPLEMENTATION_FRAMEWORK.md](IMPLEMENTATION_FRAMEWORK.md)。

独立验证前端位于相邻的 [`../memory-ui`](../memory-ui)。前端只调用 HTTP API，不读取 SQLite，也不保存模型密钥。
