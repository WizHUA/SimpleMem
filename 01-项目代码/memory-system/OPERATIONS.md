# 部署与数据维护

本实现适合单节点交付和课程 engine 嵌入。SQLite 的事务保护覆盖同一数据库文件的多个连接；推理缓存和请求合并保存在进程内。生产容量上限应以实际数据规模、模型配额和机器上的测试结果确定。

## 身份与部署

HTTP 默认采用固定主体：`MEMORY_LOCAL_TENANT` 和 `MEMORY_LOCAL_OWNER`。请求中的租户、用户头不会改变数据归属。SDK 的 `Scope` 必须由宿主认证结果构造，不能直接信任用户填写的身份。

独立 HTTP 服务上线前设置：

```dotenv
MEMORY_DEPLOYMENT_MODE=production
MEMORY_API_KEY=<至少32字符的随机密钥>
MEMORY_LOCAL_TENANT=your-tenant
MEMORY_LOCAL_OWNER=your-owner
MEMORY_ALLOWED_ORIGINS=https://your-ui.example
MEMORY_DB_PATH=/persistent-volume/memory.sqlite3
```

所有 `/api/v1/` 数据接口要求 `Authorization: Bearer <密钥>`；`/health` 保留公开可读，以便探活。生产模式缺少足够长度的密钥会拒绝启动。通过 HTTPS 反向代理接入；持久化目录须限制为服务账户可访问。密钥不要写入源码或浏览器打包产物。需要多用户前端时，由宿主服务认证用户并覆盖 `get_scope` 依赖，从认证信息生成 `Scope`，而不是将共用管理密钥下发给全部用户。此处的 Bearer 模式是单主体服务认证，不等于多租户身份管理。

配置优先采用进程环境变量；本地 `memory-system/.env` 仅填充尚未设置的变量。生产容器不要携带开发 `.env`。数值设置在启动时校验，拒绝负值、非法零值和非有限超时。

## 写入与提取

调用方为每轮传入稳定的 `request_id`。同一会话中重复提交相同内容会返回原轮次；重复 ID 携带不同内容会返回 409。SQLite 事务原子分配轮次序号并推进 session revision。

提取在一个数据库快照内读取 session 与 turns，然后在事务外执行模型调用。提交时检查 revision 和连续的待处理轮次前缀；遇到并发写入返回 409，保留 watermark 和 pending 数据，由调用方重试。模型超时、无配置、无效证据、过大状态都不会推进 watermark。不要对包含其他写入的整个工作流盲目重试；使用同一 request ID 重试轮次写入即可。

模型生成的 session 状态不能超过窗口预算的三分之一，避免一次异常提取让后续提取永远无法运行。长工具输出应先保留相关观察，再分轮写入。

增量抽取附带最多 8 个当前授权范围内的相关长期字段，目录占用最多 1200 字符预算，用于复用稳定的实体名与属性名。目录本身不是证据，候选仍必须引用本批新消息。截止日期、会议日期等事件参数保存在 `value`；没有明确生效或失效证据时，`valid_from/valid_to` 保持空值。格式、字段重复及证据约束失败共用一次模型修复机会和原请求超时；修复失败保留待处理轮次，不会静默修改用户给出的值。

## 生命周期

`retract` 使 active 或 pending 记忆退出检索，保留审计历史。`defer` 保留待审候选；当前没有直接恢复 pending 的动作，需要补充新证据、重新提取候选后再演化，原 pending 可撤回。长期事实修订采用 `supersede`，显式记录新事实生效时间和版本关系；历史版本通过审计和历史查询访问。层级展示只包含当前有效的 active 长期记忆。

`DELETE /api/v1/owner/data` 删除**当前认证主体**的全部 session、turn、memory、audit 和 group，并清理对应进程缓存。此操作不可恢复，界面或宿主应向用户明确其范围。其他租户与其他 owner 的数据保留。该接口执行数据库逻辑删除，不保证擦除 SQLite 空闲页、WAL、磁盘快照、历史备份或外部模型服务日志；有物理擦除要求时应采用加密存储及独立的密钥、备份保留策略。

## 备份与恢复

不要在运行时仅复制 `memory.sqlite3` 而忽略 WAL。通过可信运维代码调用 `await asyncio.to_thread(runtime.store.backup_to, Path("备份目标.sqlite3"))`；该方法使用 SQLite 在线备份机制，校验完整性，拒绝覆盖已有文件，包括正在使用的数据库。也可先停服务再备份数据库文件和其一致的 WAL 状态。恢复到独立路径后运行 `PRAGMA integrity_check`，使用配置指向恢复文件，检查 health、已知 session、历史版本和权限隔离。备份应与运行目录分开保存并限制访问；清除用户数据时还需按业务保留策略处理备份副本。

## 可选语义检索

不配置 embedding 时，检索明确标注 `lexical_baseline`。启用真正的语义向量视图需要独立配置 OpenAI-compatible embedding 服务：

```dotenv
MEMORY_EMBEDDING_BASE_URL=https://embedding-provider.example/v1
MEMORY_EMBEDDING_NAME=your-embedding-model
MEMORY_EMBEDDING_API_KEY=<embedding服务的密钥>
```

模型必须提供 `/embeddings` 接口，不自动复用 chat endpoint 或 chat API Key。配置一部分字段会拒绝启动；供应商故障、非法向量、维度漂移会明确报错，不会悄悄伪装为语义检索成功。默认每批32条输入，适配器保留256条内存LRU，所有批次共享超时预算。0.3.0另有持久化检索投影：权威库旁的 `*.retrieval.sqlite3` 保存FTS5和按内容哈希、模型、接口与 `MEMORY_EMBEDDING_REVISION` 分区的向量。模型原名不变而权重变化时递增revision。投影可重建，不是权威备份；用户删除同时清除其会话投影并阻止旧在途任务回填。运维的数据保留/删除策略须覆盖权威库、投影、WAL和备份。

## 自动维护与恢复

独立HTTP进程默认每300秒维护本地配置主体；`MEMORY_MAINTENANCE_INTERVAL=0`禁用。维护不会自动删除稳定事实，过期事件归档可恢复；老化强度仅供审阅。处理周期最多120秒，模型关系建议/摘要各自有30秒批次截止时间。关闭进程先取消并等待worker，再关闭模型和数据库。健康检查包含最近维护状态。

抽取事务同时写入 `evolution_jobs`，后续演化失败保留任务。再次回答、整理或周期维护会重试，不重复抽取已处理原文。`version`表示事实版本，`revision`表示同一版本的状态/证据变化；前端发送 `expected_revision` 和 `target_revision` 防止旧页面覆盖新状态。旧客户端未发送revision时仅有原版本/状态兼容检查，升级客户端后再作为并发编辑验收依据。

## 推理复用

`MEMORY_ACCELERATION_ENABLED` 控制默认加速开关；`MEMORY_INFERENCE_CACHE_TTL` 默认 60 秒；`MEMORY_INFERENCE_CACHE_ENTRIES` 默认 128。请求的 `accelerate=false` 可运行对照基线。复用只适用于相同模型、身份、会话和完整生成上下文，不将相似问题当作相同请求。多 worker 的缓存相互独立，不能将单进程命中率当作整个集群命中率。

## 验证

回答生成后，服务端返回 `answer_id`，并在同一数据库保存正文及对应的来源、检索步骤和运行阶段快照。客户端保存助手消息时只提交正文和 `answer_id`；服务端核对身份、会话与完整正文后，原子绑定 `answer_context`。客户端直接提交来源快照会被拒绝。历史消息展示其生成当时的快照，不随记忆后续更正或撤回而改写；快照只用于展示，不再次送入抽取或模型提示词。旧消息没有快照时不推测其来源。

每个主体最多保留最近 200 条尚未绑定的回答记录，无主动时间过期；超过上限时最旧记录淘汰。绑定成功后临时回答记录删除，快照保留在消息中，相同 `request_id` 仍可幂等重试。延迟首次保存且记录已淘汰，或使用另一个请求编号重复绑定同一回答，将返回明确冲突，需要重新生成。删除主体时一并删除回答记录和消息快照；既有备份仍须按运维保留策略处理。

仓库根目录执行：

```powershell
conda run -n simplemem-agentmemory python -m pytest memory-system/tests -q
```

`tests/test_hardening.py` 覆盖双连接并发轮次、拒绝跳过 watermark、旧快照冲突回滚、跨会话长期事实去重、pending 撤回、有效期展示、按主体删除、生产配置、Bearer 认证、零上下文窗口和异常模型状态。上线还需用真实宿主身份、真实模型和目标部署环境执行集成验收。
