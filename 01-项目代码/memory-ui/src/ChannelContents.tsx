import { useState } from "react";
import { ArrowUpRight, Check, FileSearch, Filter, Search } from "lucide-react";
import type { Hit, RetrievalChannel } from "./types";
import { channelIdleReason } from "./retrievalStatus";

const LABELS: Record<string, string> = {
  query: "原始问题", subject: "匹配主体", predicate: "匹配属性",
  semantic_queries: "实际语义查询", keywords: "查询关键词", lexical_terms: "实际检索词项",
  temporal_mode: "时间模式", as_of: "查询时点", scope: "作用范围", threshold: "余弦阈值",
  cosine_threshold: "余弦阈值", session_id: "会话范围", project_id: "项目范围",
  required_info: "查找的信息", evaluated_at: "评估时点", scope_policy: "范围策略", excluded_statuses: "排除状态",
};
function conditionText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未指定";
  if (value === "authorized_owner_session_project_user") return "当前授权用户的会话、项目与用户级记忆";
  if (value === "retracted") return "已撤回";
  if (value === "pending") return "待确认";
  if (value === "archived") return "已归档";
  if (value === "current") return "当前有效信息";
  if (value === "history") return "历史信息";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
function Conditions({ values, view }: { values: Record<string, unknown>; view: string }) {
  const entries = Object.entries(values);
  const primaryKeys = ["query", "required_info", ...(view === "lexical" ? ["keywords", "lexical_terms"] : view === "semantic" ? ["semantic_queries", "cosine_threshold", "threshold"] : ["subject", "predicate"])];
  const known = entries.filter(([key]) => LABELS[key] && primaryKeys.includes(key));
  const secondary = entries.filter(([key]) => LABELS[key] && !primaryKeys.includes(key));
  const extra = entries.filter(([key]) => !LABELS[key]);
  return <div className="channel-conditions"><h4><Search size={14} />这一路实际查了什么</h4>
    <dl>{known.map(([key, value]) => <div key={key}><dt>{LABELS[key]}</dt><dd>{Array.isArray(value) ? value.length ? <><div className="condition-terms">{value.slice(0, 12).map((item, index) => <span key={index}>{conditionText(item)}</span>)}</div>{value.length > 12 && <details><summary>其余 {value.length - 12} 个词项</summary><div className="condition-terms">{value.slice(12).map((item, index) => <span key={index}>{conditionText(item)}</span>)}</div></details>}</> : "未指定" : conditionText(value)}</dd></div>)}</dl>
    {!!secondary.length && <details className="channel-scope-conditions"><summary>范围、时间与其他条件</summary><dl>{secondary.map(([key, value]) => <div key={key}><dt>{LABELS[key]}</dt><dd>{Array.isArray(value) ? value.map(conditionText).join("、") || "未指定" : conditionText(value)}</dd></div>)}</dl></details>}
    {!!extra.length && <details className="channel-raw-conditions"><summary>其他实际查询条件</summary><pre>{JSON.stringify(Object.fromEntries(extra), null, 2)}</pre></details>}
  </div>;
}
function channelExplanation(channel: RetrievalChannel) {
  if (channel.status !== "complete") return channelIdleReason(channel);
  if (!channel.matched_count) return `本通道检查了 ${channel.input_count} 条记忆，没有命中。已保存的比对内容与原因见下方。`;
  return `本通道命中 ${channel.matched_count} 条记忆；命中后还需经过融合、排序和上下文预算选择。`;
}
function EvidenceLinks({ sources, view, tier, onSource }: { sources: Hit[]; view?: string; tier: string; onSource: (number: number) => void }) {
  const known = sources.map((source, index) => ({ source, index })).filter(({ source }) => source.metadata.tier === tier && (!view || Array.isArray(source.metadata.matched_views) && source.metadata.matched_views.includes(view)));
  return <div className="channel-known-sources"><h4><FileSearch size={14} />本轮已知的最终来源</h4>{known.length ? known.map(({ source, index }) => <button type="button" key={`${source.chunk_id}:${index}`} onClick={() => onSource(index + 1)}><span>{index + 1}</span><span>{source.content}</span><ArrowUpRight size={14} /></button>) : <p>本轮没有记录此处的最终来源。</p>}</div>;
}
export function LibraryEvidence({ sources, tier, onSource }: { sources: Hit[]; tier: "short" | "long"; onSource: (number: number) => void }) {
  return <section className="library-evidence" aria-label={`${tier === "short" ? "短期" : "长期"}记忆本轮来源`}><EvidenceLinks sources={sources} tier={tier} onSource={onSource} /><p className="channel-detail-note">这里展示本次回答实际选用的来源，不是整个记忆库。点击可查看回答时保存的原文与版本。</p></section>;
}
export function ChannelContents({ channels, view, sources, completed, onSource }: { channels: RetrievalChannel[]; view: string; sources: Hit[]; completed: boolean; onSource: (number: number) => void }) {
  const [tier, setTier] = useState<"short" | "long">(channels.find((channel) => channel.candidates?.length)?.tier || (sources.find((source) => Array.isArray(source.metadata.matched_views) && source.metadata.matched_views.includes(view))?.metadata.tier === "long" ? "long" : "short"));
  const [filter, setFilter] = useState<"all" | "matched" | "selected" | "unmatched">("all");
  const channel = channels.find((record) => record.tier === tier);
  const hasDetails = !!channel && !!channel.query_conditions && Object.keys(channel.query_conditions).length > 0 && Array.isArray(channel.candidates);
  const candidates = channel?.candidates || [];
  const visible = candidates.filter((candidate) => filter === "all" || filter === "matched" && candidate.matched || filter === "selected" && candidate.selected || filter === "unmatched" && !candidate.matched);
  return <div className="channel-contents">
    <div className="channel-store-tabs" aria-label="选择检索记忆库">{(["short", "long"] as const).map((item) => <button type="button" key={item} aria-pressed={tier === item} onClick={() => { setTier(item); setFilter("all"); }}>{item === "short" ? "短期库" : "长期库"}</button>)}</div>
    {channel && channel.status !== "complete" ? <p className={`channel-outcome ${channel.status}`}>{channelIdleReason(channel)}</p> : hasDetails ? <>
      <Conditions values={channel.query_conditions!} view={view} />
      <p className={`channel-outcome ${channel.status}`}>{channelExplanation(channel)}</p>
      {channel.status === "complete" && <div className="channel-candidates">
        <div className="channel-candidates-heading"><h4><Filter size={14} />实际检查的记忆</h4><span>{candidates.length} 条已保存 / {channel.candidate_total ?? channel.input_count} 条检查</span></div>
        {!!candidates.length && <div className="candidate-filters" aria-label="筛选保存的候选">{([{ key: "all", text: "全部快照" }, { key: "matched", text: "已命中" }, ...(completed ? [{ key: "selected", text: "最终入选" }] : []), { key: "unmatched", text: "未命中" }] as Array<{ key: typeof filter; text: string }>).map((option) => <button type="button" key={option.key} aria-pressed={filter === option.key} onClick={() => setFilter(option.key)}>{option.text}</button>)}</div>}
        <div className="candidate-list" tabIndex={visible.length ? 0 : undefined} aria-label="保存的检查快照">{visible.map((candidate) => {
          const sourceIndex = candidate.selected ? sources.findIndex((source) => source.metadata.memory_id === candidate.memory_id && source.metadata.version === candidate.version && (source.metadata.revision === undefined || source.metadata.revision === candidate.revision)) : -1;
          const selectionLabel = candidate.selected && completed ? "最终入选" : candidate.matched ? completed ? "命中，未入选" : "已命中，等待筛选" : "未命中";
          return <article className={`channel-candidate ${candidate.matched ? "matched" : "unmatched"}`} key={`${candidate.memory_id}:${candidate.version}:${candidate.revision}`}>
            <header><span className={`candidate-state ${candidate.selected && completed ? "selected" : ""}`}>{candidate.selected && completed && <Check size={12} />}{selectionLabel}</span><span>v{candidate.version}{candidate.score !== null && ` · 匹配分 ${candidate.score.toFixed(3)}`}</span></header>
            <p className="candidate-fact">{candidate.content}</p>{candidate.content_truncated && <small>内容已截断，以上为保存的预览。</small>}
            {(candidate.subject || candidate.predicate) && <div className="candidate-fields"><span>{candidate.subject || "未记录主体"}</span><span>{candidate.predicate || "未记录属性"}</span>{candidate.value && <span>{candidate.value}</span>}</div>}
            <p className="candidate-reason">{candidate.reason || "本次记录没有附带匹配原因。"}</p>
            {sourceIndex >= 0 && <button className="candidate-source" type="button" onClick={() => onSource(sourceIndex + 1)}>查看原文证据 {sourceIndex + 1}<ArrowUpRight size={13} /></button>}
          </article>;
        })}</div>
        {!visible.length && <p className="channel-empty">{candidates.length ? "保存的快照中没有符合此筛选的记录。" : "本轮没有保存可展示的检查条目。"}</p>}
        {channel.candidates_truncated && <p className="channel-truncated">本次只保存 {candidates.length} / {channel.candidate_total ?? channel.input_count} 条检查快照（上限 {channel.candidate_limit ?? 20} 条），优先保留命中内容。列表不是全部候选，筛选仅作用于已保存快照。</p>}
        <p className="channel-detail-note">匹配分属于当前通道，不代表事实可信度。内容为回答当时的快照预览。</p>
      </div>}
    </> : <div className="channel-history-note"><p>{channel ? "这条历史记录没有保存实际查询条件与候选内容，无法回溯当时未入选的记忆。" : completed ? "没有找到此库的通道明细，不能据此推断检查过哪些记忆。" : "等待该记忆库返回实际查询条件和候选内容。"}</p><EvidenceLinks sources={sources} view={view} tier={tier} onSource={onSource} /></div>}
  </div>;
}
