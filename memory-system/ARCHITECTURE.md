# Architecture

## 分层

```text
Transport: api.py | integrations/rag_sdk.py
    只处理协议、可信上下文和错误映射

Application: runtime.py | short_term.py | long_term.py | retrieval.py
    编排短期窗口、意图查询、长期动作与回答

Domain: models.py | ports.py
    跨模块数据契约与外部能力接口

Infrastructure: store.py | providers.py
    SQLite 和模型适配
```

依赖只向下：核心领域不导入 FastAPI、宿主 SDK 或 SimpleMem。`api.py` 和 `rag_sdk.py` 调用同一个 `MemoryRuntime`。

## 关键闭环

```text
append turn -> recent raw STM -> search/answer
           -> 5 pending turns or explicit trigger -> model extraction
           -> short memories + state patch + watermark
           -> reviewed evolution action -> long versions
           -> reference grouping / future external index
```

查询使用 `QueryPlan(route, semantic_queries, keywords, subject, predicate, depth)`。`depth` 决定目标结果数和候选预算，调用者 `top_k` 是最终上限。H-MEM 的 `hierarchy_path` 只用于组织，当前不会取代 SimpleMem 式规划或强制逐层 beam。

## 数据真实性

- 原始 turn 是证据；结构化 memory 是派生结论。
- 每条抽取结果至少引用一个本批新 turn，旧语境只能补充指代证据。
- 用户/工具可以建立 `stated/observed`；assistant 只能先保留为计划、假设或推断。
- 长期晋升必须显式执行；`durable` 是候选提示，不是自动授权。
- `superseded` 表示过去曾有效，`retracted` 表示原陈述错误。
- H-MEM 分组和将来的层级摘要是可重建投影，不是权威事实。

## 当前简化

- SQLite 面向单节点计划/验证阶段；多实例部署前迁移 PostgreSQL 或实现进程间写锁。
- 词法检索按授权 owner 的小集合扫描；大规模阶段改为 FTS/Qdrant 候选查询。
- token 预算使用保守字符数，接入实际模型时替换为该模型 tokenizer。
- 语义 relation merge 暂不自动执行；当前 merge 仅接受字段和时间完全相同的事实。
- H-MEM 当前只按最多三级 `hierarchy_path` 建引用组，不生成可能失真的高层事实摘要。

## 多人协作约束

| 负责方向 | 主要文件 | 不应修改的边界 |
|---|---|---|
| 短期抽取 | `short_term.py`, `providers.py` | 不直接写 SQL，不决定 owner |
| 检索 | `retrieval.py` | 不修改事实状态，不生成答案事实 |
| 生命周期 | `long_term.py`, `store.py` | 不接受无 expected_version 的更新 |
| HTTP | `api.py` | 不复制业务规则，不把 Header 当认证 |
| SDK | `integrations/rag_sdk.py` | 不修改 SDK 签名，不捕获 search 错误为空 |
| 前端 | 未来独立目录/仓库 | 只依赖 OpenAPI，不读取 SQLite |

共享 `models.py` 或 API Schema 变更时，要同步更新测试和前端契约。模型提示词版本、Embedding 版本和阈值都应进入实验配置记录。

