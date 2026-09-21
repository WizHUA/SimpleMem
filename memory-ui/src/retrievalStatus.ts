import type { RetrievalChannel } from "./types";

export function channelIdleReason(channel: RetrievalChannel): string {
  if (channel.status === "disabled") return channel.view === "semantic"
    ? "语义检索为可选增强，当前未配置向量服务。"
    : "该通道当前未启用。";
  switch (channel.detail) {
    case "exact_field_lookup": return "本次为明确的单字段查询，已有精确匹配，省去向量检索。";
    case "no_lexical_terms": return "本次查询没有可用于全文匹配的词项。";
    case "short_evidence_sufficient": return "短期记忆已满足本次检索需求，无需补查长期库。";
    case "subject_and_predicate_required":
    case "missing_subject_predicate": return "本次查询未形成完整的主体与属性条件，未触发精确匹配。";
    case "candidate_budget_exhausted": return "本次候选预算已用完，未继续检索。";
    case "no_eligible_memories": return "当前范围与时间条件下没有可检查的记忆。";
    case "route_none": return "本次查询无需检索记忆。";
    case "empty_query_or_zero_cap": return "查询为空或检索上限为零。";
  }
  return /[\u4e00-\u9fff]/.test(channel.detail) ? channel.detail : "本次未执行该通道，记录未提供具体原因。";
}
