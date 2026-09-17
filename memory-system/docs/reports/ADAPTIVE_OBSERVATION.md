# 动态检索与回答复用真实观察

模型：deepseek-flash；Embedding 启用：False。临时合成数据库，未修改用户数据。

首个请求省略 top_k，记录计划与真实通道；后续比较采用固定数据库和会话上下文，不追加助手消息，先禁用缓存基线，再启用缓存冷、热各一次。

问题：项目星舟的负责人、交付日期和报告语言分别是什么？

原始回答：项目星舟的负责人是林然【来源1】，交付日期是2026年12月18日【来源2】，报告使用中文【来源3】。

```json
{
  "planned_depth": 3,
  "required_info_count": 3,
  "candidate_limit": 18,
  "safety_cap": 20,
  "target_k": 3,
  "selected_k": 3,
  "token_limit": 3000,
  "used_tokens": 358,
  "selection_policy": "slot_diversity_complete_evidence_bundles_within_budget",
  "score_semantics": "relevance_not_truth_confidence"
}
```

|层|通道|状态|输入|命中|最终入选|
|---|---|---|---:|---:|---:|
|short|lexical|complete|3|3|3|
|short|symbolic|skipped|0|0|0|
|short|semantic|disabled|0|0|0|
|long|semantic|disabled|0|0|0|
|long|lexical|skipped|0|0|0|
|long|symbolic|skipped|0|0|0|

|请求|缓存状态|检索 ms|生成 ms|总计 ms|避免模型调用|
|---|---|---:|---:|---:|---:|
|baseline|disabled|1238.2|788.0|2028.2|0|
|cold|miss|733.8|856.6|1591.5|0|
|warm|hit|1169.7|0.0|1181.2|1|

检查：`{"adaptive_plan_reported": true, "all_facts_answered": true, "unconfigured_semantic_not_claimed": true, "baseline_does_not_cache": true, "cold_miss": true, "warm_hit": true}`

该样本只验证实际路径和观测数据，不代表统计性能倍数。缓存仍重新执行规划与检索；模型规划若改变生成上下文，热请求可能继续 miss，报告保留实际结果。长度预算为保守字符估计，通道分数不是事实置信度。完整响应见同名 JSON。