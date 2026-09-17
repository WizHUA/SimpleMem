import type { AnswerResult, QueryStep } from "./types";

const NOTES: Record<string, [string, string]> = {
  multi_slot_recall: [
    "分别寻找多个信息项",
    "问题需要多个属性，检索没有用单一字段限制其他信息的召回。",
  ],
  fts5_projection: [
    "全文索引已筛选候选",
    "先从授权范围内筛选词项候选，再进行词法相关性排序。",
  ],
  vector_projection: [
    "向量检索已执行",
    "复用已有记忆向量，并为缺失内容生成向量后比较语义相似度。",
  ],
  long_term_searched: [
    "已补充长期记忆",
    "本轮实际查询了长期库；是否找到可用证据请看入选来源。",
  ],
  short_evidence_uncertain: [
    "短期信息不足以单独作答",
    "无法确认短期记忆能完整支持答案，因此补充了一次长期检索。",
  ],
  short_only: [
    "短期记忆已满足精确查询",
    "命中无歧义的单字段事实，本轮没有查询长期库。",
  ],
  required_info_coverage_unverified: [
    "仍需核对证据是否充分",
    "相似度命中不等于已证明所有信息项；请结合原文和回答引用核对。",
  ],
  local_override: [
    "本次例外优先",
    "会话或项目中的局部例外优先使用，长期默认事实保持原样。",
  ],
  symbolic_field_miss: [
    "精确字段未命中",
    "主体与属性未找到精确匹配，已在相同授权范围内继续召回。",
  ],
  model_planner_schema_error: [
    "已使用规则规划",
    "模型返回的规划格式未通过校验，本轮改用规则规划。",
  ],
  rule_planner: [
    "使用规则规划",
    "当前意图来自规则；语义意图和证据充分性尚未经过模型验证。",
  ],
  no_supported_result_within_budget: [
    "没有可用的入选证据",
    "在当前相关性与上下文预算约束下，没有选出可支持回答的记忆。",
  ],
  result_count_limited_by_context_budget_or_relevance: [
    "结果数量受到限制",
    "上下文预算或相关性筛选限制了入选数量，并非总是填满检索上限。",
  ],
  top_k_cannot_guarantee_exhaustive_results: [
    "本次结果不保证穷尽",
    "检索上限只返回部分相关记忆，不能当作“全部记录”的完整清单。",
  ],
  "semantic_cosine_threshold_0.35_requires_development_set_calibration": [
    "语义阈值仍需校准",
    "本轮语义召回采用 0.35 的余弦阈值；这是检索阈值，不是事实可信度。",
  ],
  pending_conflict_requires_confirmation: [
    "存在待确认的不同说法",
    "相关候选仍有冲突，应先审阅原文；候选不会自动成为已确认事实。",
  ],
  relation_model_unavailable: [
    "辅助关系判断未完成",
    "已保存的抽取结果保留；关系模型本次不可用，待确认候选需要审阅。",
  ],
  summary_model_unavailable: [
    "辅助摘要未完成",
    "本次模型摘要不可用，带来源的抽取式分组仍可检查。",
  ],
  evolution_pending_retry: [
    "记忆演化等待重试",
    "事实抽取已保存，后续演化尚未完成；服务将在后续处理时重试。",
  ],
  "Answer contains an invalid citation; it was excluded from citations": [
    "已排除无效引用",
    "回答中出现超出来源范围的编号，未将该编号列为有效引用。",
  ],
};

function numberIn(raw: string, key: string) {
  return raw.match(new RegExp(`(?:^|[ ;:])${key}=(\\d+)`))?.[1];
}

export function TraceNotes({ warnings }: { warnings: string[] }) {
  if (!warnings.length) return null;
  const known = warnings
    .map((raw) => {
      const note = NOTES[raw.split(":")[0]];
      if (!note) return null;
      let detail = note[1];
      if (raw.startsWith("fts5_projection:")) {
        const ranked = numberIn(raw, "reranked"),
          eligible = numberIn(raw, "eligible");
        if (ranked !== undefined && eligible !== undefined)
          detail += ` 本次 ${eligible} 条合格记忆中有 ${ranked} 条进入词法重排。`;
      }
      if (raw.startsWith("vector_projection:")) {
        const reused = numberIn(raw, "reused"),
          documents = numberIn(raw, "documents");
        if (reused !== undefined && documents !== undefined)
          detail += ` ${documents} 条记忆中复用了 ${reused} 条向量。`;
      }
      return { title: note[0], detail };
    })
    .filter((note) => note !== null);
  const unknown = warnings.length - known.length;
  return (
    <section className="trace-notes" aria-label="运行说明">
      <h3>本轮做了哪些取舍</h3>
      {known.map((note, index) => (
        <details key={index}>
          <summary>{note.title}</summary>
          <p>{note.detail}</p>
        </details>
      ))}
      {!!unknown && (
        <p className="measurement-note">
          另有 {unknown} 条服务记录，可展开原始记录查看。
        </p>
      )}
      <details className="technical-detail">
        <summary>原始记录（运行说明）</summary>
        <pre>{warnings.join("\n")}</pre>
      </details>
    </section>
  );
}

export const VIEW_LABELS: Record<string, [string, string]> = {
  lexical: ["词法匹配", "根据实际词项命中与相关性排序召回。"],
  semantic: ["语义匹配", "由向量余弦相似度召回。"],
  symbolic: ["字段精确匹配", "主体与属性同时匹配。"],
  hierarchy: ["层级标签匹配", "查询词命中记忆的领域、类别或线索标签。"],
  entity_link: [
    "实体关系扩展",
    "沿有原文支持的显式关系扩展相关记忆，最多两跳。",
  ],
  entity_link_support: ["关系链支撑证据", "为实体关系扩展结果保留的前置证据。"],
};

export function traceMode(mode: string) {
  return (
    (
      {
        lexical_baseline: "词法与结构化检索（未启用向量）",
        hybrid: "混合检索配置（含可用向量通道）",
      } as Record<string, string>
    )[mode] || "服务返回的检索模式"
  );
}

export function readableStep(step: QueryStep, trace: Pick<AnswerResult, "plan" | "retrieval_mode"> & { context_tokens?: number }) {
  const n = step.input_count,
    out = step.output_count;
  switch (step.action) {
    case "intent_plan":
      return {
        title: "明确查询范围",
        detail: `规划需要 ${out} 个信息项，${trace.plan?.temporal_mode === "history" ? "查询历史版本" : "查询当前有效记忆"}；规划深度 ${trace.plan?.depth}。`,
        count: `${n} 个问题 → ${out} 个信息项`,
      };
    case "semantic_lexical_symbolic_recall":
      return {
        title:
          step.phase === "long_retrieval" ? "从长期库召回" : "从短期工作区召回",
        detail: `在作用范围与时间约束内，按可用匹配通道寻找候选。${trace.retrieval_mode === "lexical_baseline" ? "本轮未启用向量检索。" : "通道是否执行和实际命中，以运行说明及来源标签为准。"}`,
        count: `${n} 条输入记忆 → ${out} 条候选`,
      };
    case "evidence_entity_expansion":
      return {
        title: "沿显式关系补充证据",
        detail:
          "最多沿两跳事实关系扩展，必须保留支撑来源，不根据相似名称推断身份。",
        count: `${n} 条可检查记忆 → ${out} 条关联结果`,
      };
    case "deduplicate_scope_time_version":
      return {
        title: "合并并核对适用版本",
        detail:
          "对已按范围和时间过滤的结果去重、排序；局部例外优先，年龄只有限影响排序，事实与偏好不衰减。",
        count: `${n} 条召回结果 → ${out} 条排序结果`,
      };
    case "dynamic_k_and_token_budget":
      return {
        title: "按预算选择回答证据",
        detail: `结合信息需求、检索上限与上下文长度选择证据，保留关系链。${trace.context_tokens !== undefined ? `当前上下文估算 ${trace.context_tokens} tokens。` : ""}`,
        count: `${n} 条候选 → ${out} 条入选`,
      };
    default:
      return {
        title: /[\u4e00-\u9fff]/.test(step.action)
          ? step.action
          : "服务执行步骤",
        detail: /[\u4e00-\u9fff]/.test(step.detail)
          ? step.detail
          : "该步骤的详细含义请查看服务原始记录。",
        count: `输入 ${n} → 输出 ${out}`,
      };
  }
}
