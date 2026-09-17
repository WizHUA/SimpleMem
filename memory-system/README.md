# Agent Memory Service

独立的长短期记忆后端，配套相邻 `memory-ui` 展示前端。开发分支为 `full-forward`。系统支持证据约束的自动抽取、跨会话记忆、事实更正与历史版本、混合检索、来源摘要和精确上下文推理复用。

当前面向单节点交付。真实课程 Engine 集成不在本轮推进范围；保留的适配器及契约测试不能替代真实宿主验收。公开基准、完整语义忠实度和生产容量也不能由本地回归测试替代。

- [架构](ARCHITECTURE.md) · [实现框架与模型调用](IMPLEMENTATION_FRAMEWORK.md) · [生命周期机制](docs/LIFECYCLE_MECHANISMS.md)
- [课程需求](docs/COURSE_REQUIREMENTS.md) · [验证记录](docs/VERIFICATION.md) · [交付入口](../DELIVERY.md)
- [技术报告](docs/技术报告.md) · [演示脚本](docs/答辩演示脚本.md) · [部署、备份与数据治理](OPERATIONS.md)

## 当前机制

| 部分 | 具体方案 |
|---|---|
| 短期记忆 | 原始事件、任务状态、摘要、结构化事实；每批最多 5 个新 turn、最多 15 个已处理上下文 turn |
| 自动准备 | answer 默认处理待抽取积压，search 保持只读；手动 extract 仍可使用 |
| 证据规则 | 原文位置和引文校验；问句不能成为肯定事实，助手猜测不能污染长期记忆 |
| 长期演化 | 自动安全晋升、同事实证据合并、现实更新、错误纠正、冲突待决、归档与恢复 |
| 一致性 | SQLite WAL、事务、请求幂等、事实版本、状态 revision、审计；演化 outbox 支持失败重试和重启恢复 |
| 检索 | 模型意图规划、短期优先、FTS5/BM25、符号约束、可选向量、两跳显式关系证据扩展、动态预算 |
| 分层维护 | Domain/Category/Trace 来源摘要、可选模型概括、缓存失效、年龄与证据保留策略 |
| 动态展示 | NDJSON 真实阶段事件，返回规划、候选、选择、来源和缓存观测；不是隐藏思维链或逐 token 流 |
| 推理复用 | 精确上下文 TTL/LRU 缓存及相同在途调用合并，回答返回前复验版本和有效期 |

SQLite 保存权威事实；FTS、向量和摘要均为可重建投影。当前仍读取授权 owner 的权威快照，再使用索引筛选候选，没有实现 ANN 或分布式检索。未配置向量服务时明确使用 `lexical_baseline`。

## 安装与启动

```powershell
# 从 SimpleMem 根目录执行，使用既有 Conda 环境。
cd memory-system
conda run --no-capture-output -n simplemem-agentmemory python -m pip install -e ".[dev]"
conda run --no-capture-output -n simplemem-agentmemory python -m agent_memory
```

后端默认地址为 `http://127.0.0.1:8088`，OpenAPI 页面为 `/docs`。在另一个终端启动前端：

```powershell
# 从 SimpleMem 根目录执行。
cd memory-ui
pnpm install --frozen-lockfile
pnpm dev
```

前端默认位于 `http://127.0.0.1:5173`，Vite 将 `/api`、`/health` 代理至后端。后端换端口时，通过前端进程环境变量 `MEMORY_UI_PROXY_TARGET` 指定实际地址。

在私有 `memory-system/.env` 中配置模型：

```dotenv
MEMORY_MODEL_BASE_URL=<OpenAI兼容服务地址>
MEMORY_MODEL_NAME=<账号可用的模型名>
MEMORY_MODEL_API_KEY=<仅保存在后端的密钥>
MEMORY_MODEL_TIMEOUT=90
MEMORY_MODEL_MAX_TOKENS=4096
MEMORY_MAINTENANCE_INTERVAL=300
```

现有进程环境优先于 `.env`，修改配置后需要重启。`GET /health` 返回实际模型、提供商和语义检索启用状态。真实 Embedding 使用独立的 `MEMORY_EMBEDDING_BASE_URL`、`MEMORY_EMBEDDING_NAME`、`MEMORY_EMBEDDING_API_KEY`。模型密钥不得放入前端、Git 或请求日志。

未配置模型时仍可创建会话、记录事件和执行词法查询；回答与抽取明确返回不可用状态。独立 HTTP 服务启动周期维护 worker，默认间隔 300 秒，设为 0 禁用。维护只面向当前配置 principal，不代表多租户后台调度已完成。

默认服务使用固定本地身份。独立生产模式要求 `MEMORY_DEPLOYMENT_MODE=production` 和至少 32 字符的 `MEMORY_API_KEY`，数据接口验证 Bearer；共用服务密钥不构成完整多用户身份系统。

## API 顺序与失败语义

```text
POST /api/v1/sessions
POST /api/v1/sessions/{id}/turns
POST /api/v1/answer                           默认先自动整理待处理记录
POST /api/v1/sessions/{id}/answer/stream       同一业务流程，返回NDJSON阶段事件
POST /api/v1/sessions/{id}/extract             可选手动整理
POST /api/v1/search                           只读检索
GET  /api/v1/memories
POST /api/v1/memories/{id}/evolve
GET  /api/v1/memories/{id}/history
POST /api/v1/maintenance
GET  /api/v1/groups
GET  /api/v1/hierarchy
```

调用方先保存用户输入，再请求回答；需要保留助手输出时另行追加。answer 不隐式写回用户和助手消息，避免重试生成重复事件。turn 使用稳定 `request_id`：同内容重试幂等，同 ID 不同内容返回 409。

抽取模型或证据校验失败不推进水位。抽取已提交而演化失败时，返回演化警告并保留持久化任务，后续调用可恢复；规划或生成失败不会撤销已提交抽取。不能把所有超时都解释为“系统完全未发生写入”。

演化支持 `promote/merge/supersede/correct/retract/defer/archive/restore/activate`。接口要求 `expected_version`；新调用方同时传入 `expected_revision`，目标动作传入目标版本与 revision，以拒绝状态往返后的旧页面操作。归档保留历史和证据；恢复不修改原有效期。`pending` 是未解决候选，不可默认为当前正确事实。

NDJSON 返回真实 `stage` 及最终 `result/error`，包括阶段耗时。当前回答文本生成完成后统一返回，不是逐 token 供应商流。界面展示执行轨迹和证据，不展示模型隐藏思维链。

## 加速的准确口径

answer 接受 `accelerate: true/false`。复用只避免相同完整上下文的最终生成调用，每次仍执行授权和新鲜查询规划、检索。默认缓存 TTL 为 60 秒、最多 128 项；相同在途请求共享一次生成。状态、来源、上下文变化后不能继续按原条件命中。

前端可在不追加对话事件的前提下比较基线与加速。正常聊天持续改变上下文，命中率不能由重复重跑实验推断。阶段计时、生成调用节省和保守字符预算分别报告，字符数不是模型实际计费 token。当前没有 GPU 算子优化、KV Cache 管理或投机解码。

## 验证

```powershell
# 在 memory-system 目录执行。
conda run --no-capture-output -n simplemem-agentmemory python -m pytest -q
conda run --no-capture-output -n simplemem-agentmemory python -m ruff check src tests scripts
conda run --no-capture-output -n simplemem-agentmemory python -m ruff format --check src tests scripts
```

普通回归测试不调用真实模型服务。真实模型脚本单独执行，其配置、耗时、失败和结果见 [验证记录](docs/VERIFICATION.md)。历史 [真实冒烟](docs/LIVE_SMOKE.md)、[十题开发小集](docs/LIVE_QUALITY.md)和[合成性能记录](docs/EVALUATION.md)保留原实验条件，不自动代表本次版本的全面验收结论。

重点回归覆盖“zfc 是飞猪吗”无证据询问、别名陈述、临时约束、跨会话保存、明确更正、冲突拒猜、长积压、超时、演化恢复、归档恢复、权限隔离和缓存失效。公开长期对话基准、语义忠实度评审和真实生产容量仍需独立实验。

## Engine 边界

可选 `agent_memory.integrations.rag_sdk` 支持课程契约的 search/generate/generate_stream，核心不依赖宿主启动。旧 SDK generate_stream 仍为缓冲兼容输出。真实宿主未在本轮集成；现有契约测试与 [SDK 说明](docs/SDK_INTEGRATION.md)仅作为后续接入依据。
