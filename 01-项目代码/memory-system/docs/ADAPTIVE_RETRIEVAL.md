# 动态检索预算与真实通道观测

依据工作区《参考资料/论文/literature-review.md》第 57、85 行，读取阶段应按问题复杂度分配证据数量，结合语义、词法与符号检索。本实现沿用约 3 至 20 条的深度范围；不是把前端固定的 Top-K 改成另一个固定数。

## 动态 K

HTTP `top_k` 现在默认 `null`，也可省略；表示由查询计划决定目标数量。它仍接受 0 至 100，供高级调用方设置上限，其中 0 显式关闭检索。系统同时应用配置的 `max_top_k`（默认 20）安全上限。

算法为：

1. 模型计划提供 `depth`、`required_info`、范围和时间条件；无模型或计划结构不合法时使用有明确标识的规则计划。
2. 动态深度 `d = max(3, min(20, max(depth, len(required_info))))`。
3. 目标 `target_k = min(d, max_top_k, top_k)`；省略的 `top_k` 不参与限制。
4. 候选预算 `min(max_candidates, 6*d)`，默认 `max_candidates=120`。短期先查并保留长期补充配额；仅严格满足单字段短期查询时省略长期检索。
5. 根据字段词项覆盖分散选择，保留完整的关系支持证据包，受长度预算约束。没有足够相关证据时不凑满 K，证据包或长度不允许时减少实际数量。

这是一套有界、可审计的预算策略，并不声称每个字段已经得到语义证明。BM25、余弦相似度和符号匹配表示相关性，不是“事实可信度”。当前语义阈值 0.35 尚需开发集校准，不能在前端画成置信度百分比。预算计数采用保守字符估计，尚非 tokenizer 精确计数。

`SearchResponse`、`AnswerResponse`、持久 `AnswerContext` 与检索阶段事件携带 `dynamic_k`：`planned_depth`、`required_info_count`、`candidate_limit`、`safety_cap`、`target_k`、`selected_k`、`token_limit`、`used_tokens`、`selection_policy`、`score_semantics`。阶段中的 selected_k 为选择完成前的零值，最终响应才是实际入选数。

## 三路通道

返回 `channels` 数组，每项包含：

|字段|含义|
|---|---|
|view|semantic、lexical、symbolic|
|tier|short、long|
|status|complete、disabled、skipped|
|input_count|该通道实际处理的候选数；词法为 FTS5 缩减后进入 BM25 的数量|
|matched_count|该通道命中的记忆数，发生在最终候选截断前|
|selected_count|最终入选证据中匹配该通道的数量，完成选择前为零|
|detail|真实执行机制或跳过原因|

词法通道执行 FTS5 候选选择及 BM25 评分；符号通道需要同时具备主体和属性；语义通道只有已配置 Embedding 且实际编码与比较后才标 complete。未配置为 disabled，无合格记忆或预算不足为 skipped。不同通道可命中同一记忆，数量不能简单相加当作独立证据数。

短期召回完成事件含三项，长期召回完成事件含累积六项；最终响应补充实际入选数。当前词法/符号先执行、语义随后执行，前端不得用动画暗示三路已经真实并发，也不得将禁用通道显示为已执行。实体关系扩展和层级提示属于额外召回机制，不伪装成语义向量命中。

## 通道内容快照

每个通道额外提供实际查询条件 `query_conditions` 和最多 20 条 `candidates`。样本来自本轮真正送入通道的合格记忆，先展示命中项；不会为了展示补进跨会话、撤回、待确认或已失效的记录。词法通道展示 FTS5 之后实际接受 BM25 检查的集合，不声称覆盖全部库记录。`candidate_total` 为实际检查总数，`candidate_limit=20`，`candidates_truncated` 明确列表是否不完整。

每项包含独立记忆 ID、version、revision、subject、predicate、value、content、matched、selected、score、reason。内容预览最多 500 字，content_truncated 标明截断；值预览最多 500 字。matched 表示本通道是否命中，selected 由最终证据的 ID、版本和修订号确认，不从数量推测。符号零命中时仍能对比实际的主体/属性条件和受检事实，而不是只看一个 0。分数仍为相关性，语义分数为余弦映射值。

这些快照随回答来源保存，之后回看不重新扫描当前数据库或猜测旧候选；旧回答没有明细时前端应明确说明。通道未执行则不生成候选样本。

## 实体记忆视图

`GET /api/v1/entities` 在读取时将长期记忆按精确规范化主体（去首尾空格、忽略大小写）和 scope_type/scope_id 归组。同一 zfc 的“别名”和“特性”会成为一张实体卡，内部各属性仍保存独立版本和证据。该投影不调用模型、不改写原始记录、不需要维护按钮；现有记录立即生效。

实体返回 entity_id、subject、scope_type、scope_id、summary、facts、pending_facts、conflict_predicates、updated_at。当前有效事实参与正文，待确认项独立展示；同属性多个值或存在待确认版本时，概览只标“待核对”，不把矛盾当成联合事实。计划、假设和推断保持相应标签。已撤回、归档、过期和未来生效记录不进入当前视图；有明确未来替代时间的旧版本在其有效期内仍可见。完整历史仍通过原始记忆历史接口查看。

此接口保持认证 tenant/owner 隔离；用户自己的不同项目与会话 scope 各自成卡，不跨作用范围合并，也不做没有证据的同义主体归并。

## 会话导航（接口）

`GET /api/v1/sessions` 返回认证作用域下的 `Session[]`，按服务端 `updated_at` 降序；写入事件及会话状态更新刷新此时间，不能通过客户端事件时间控制排序。旧记录缺少 updated_at 时回退 created_at。接口复用现有认证与 tenant/owner 绑定，不接受客户端身份覆盖；当前无分页，适合单节点小规模交付。

验证覆盖默认动态 K 超过 10、显式安全上限、字段数量提高深度、不足证据不凑数、禁用语义、缺失符号条件、三路真实数量、空库跳过，以及会话列表认证、隔离和更新排序。测试见 `tests/test_dynamic_retrieval_channels.py` 与 `tests/test_session_listing.py`。
