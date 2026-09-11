# Development Roadmap

## Phase 0：当前骨架

完成标准：本地 API 可启动；模型未配置时仍能记录与查近期原文；所有单元测试通过；SDK shape test 通过但明确不等于宿主官方验证。

## Phase 1：短期记忆

- 准备不少于 50 个中文标注窗口，覆盖否定、数字、相对时间、工具结果和跨轮指代。
- 接入宿主模型或兼容服务，测量 memory unit 与 evidence span 的 Precision/Recall/F1。
- 替换字符 token 估算，并根据开发集调节 5/15 轮和 token 压力触发。
- 增加显式阶段结束、用户纠正、工具完成触发；并发提交继续使用 session revision。

通过条件：抽取失败水位不前进；纯闲聊可返回空；所有事实能回溯原文；临时任务限制不被标为用户全局事实。

## Phase 2：长期记忆与进化

- 实现 `RelationProposal`：duplicate/complement/change/retraction/independent/uncertain。
- 先按 owner/scope/subject/predicate 缩小候选，LLM 只判断有歧义的小集合。
- 保留当前 `SQLiteStore.evolve` 的 CAS 和明确动作执行器。
- 在需要真正双时间查询时增加不可变 state revision 表；当前 audit 支持版本历史但不是完整双时间数据库。
- 完善派生记忆的 `derived_from` 和撤回传播。

通过条件：当前状态、指定历史时点、原陈述撤回、延迟补录和任务局部例外测试全部正确。

## Phase 3：大规模检索与 H-MEM

- 接入宿主 Embedding 和 Qdrant 新集合；只从授权 owner/scope 取候选。
- 保留三视图和动态 K，校准语义阈值，不使用排名伪分。
- 将引用组升级为 Domain/Category/Trace/Episode 投影，父摘要 dirty 后异步刷新。
- H-MEM 作为 long 路径候选组织，保留少量 flat leaf fallback。

先比较 Flat-SimpleMem 与 HMEM-index+fallback 在不同数据规模下的 Recall@K、路径漏检率、P95 和维护成本；没有证明收益前保持 feature flag 关闭。

## Phase 4：宿主和前端

- 在真实宿主接入 `rag_sdk.py`，使用鉴权后的 `Scope` 和已授权 session。
- 跑 `validate_plugin`，另写断言 search 五字段、分数、K、错误传播的 HTTP 测试。
- 根据 OpenAPI 单独开发前端：会话、当前 STM、检索 trace、历史版本和维护状态即可。
- 生产部署加入数据库迁移、备份、指标、日志脱敏、限流和内部认证。

## 推荐的首批任务

| 优先级 | 任务 | 负责人接口 | 验证 |
|---:|---|---|---|
| P0 | 宿主身份和 session 授权设计 | `api.get_scope` | 两用户同 query 永不互见 |
| P0 | 50 个抽取标注样本 | `MemoryModel.extract` | 单元与 evidence F1 |
| P0 | 实际 tokenizer | `estimate_tokens` | 上下文始终不超模型上限 |
| P1 | 宿主 LLM `CallableModel` | `providers.py` | 超时/Schema/失败不丢水位 |
| P1 | Qdrant 候选适配 | `MemoryRuntime.search` | 先授权过滤再 LIMIT |
| P1 | 关系分类 Proposal | `LongTermMemory` | 不确定时 defer，不自动覆盖 |
| P2 | H-MEM 层级摘要 | `reference_group` 投影 | 与 flat 检索做规模曲线 |
| P2 | 前端 | OpenAPI | 不直接耦合存储内部结构 |

