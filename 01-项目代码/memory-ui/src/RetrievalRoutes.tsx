import { useId, useState, type CSSProperties } from "react";
import { ArrowDown, ArrowRight, Braces, Database, Fingerprint, Layers3, ScanText, Search, SlidersHorizontal } from "lucide-react";
import type { DynamicK, Hit, QueryPlan, QueryStep, RetrievalChannel } from "./types";
import "./RetrievalRoutes.css";
import { ChannelContents, LibraryEvidence } from "./ChannelContents";
import { channelIdleReason } from "./retrievalStatus";

export type { RetrievalChannel } from "./types";
type View = RetrievalChannel["view"];
const VIEWS = [
  { id: "semantic" as View, name: "语义检索", method: "向量相似度", icon: Fingerprint, description: "对查询与合格记忆的向量计算余弦相似度，复用已有记忆向量；当前召回阈值为 0.35，相似度不代表事实可信度。" },
  { id: "lexical" as View, name: "词法检索", method: "全文索引 · BM25", icon: ScanText, description: "FTS5 先筛选词项候选，再用 BM25 排序；中文使用字组，英文使用词项。层级路径匹配作为额外线索保留。" },
  { id: "symbolic" as View, name: "符号检索", method: "主体与属性匹配", icon: Braces, description: "在作用范围与有效时间约束内匹配明确的主体和属性。精确匹配不会因全文候选截断而丢失；未形成完整主体/属性条件时跳过该通道。" },
];
const TIERS = [{ id: "short" as const, name: "短期记忆", note: "先查当前会话" }, { id: "long" as const, name: "长期记忆", note: "按需补充" }];
function hitsFor(sources: Hit[], view: View, tier?: string) {
  return sources.filter((source) => (!tier || source.metadata.tier === tier) && Array.isArray(source.metadata.matched_views) && source.metadata.matched_views.includes(view));
}

export function RetrievalRoutes({ sources, steps, channels = [], plan, phase, live, retrievalMode, dynamicK, replaying = false, onSource }: {
  sources: Hit[];
  steps: QueryStep[];
  channels?: RetrievalChannel[];
  plan?: QueryPlan | null;
  phase?: string;
  live: boolean;
  replaying?: boolean;
  retrievalMode?: string;
  dynamicK?: DynamicK | null;
  onSource: (number: number) => void;
}) {
  const id = useId().replace(/:/g, "");
  const [focused, setFocused] = useState<View | null>(null);
  const [focusedLibrary, setFocusedLibrary] = useState<"short" | "long" | null>(null);
  const selectLibrary = (tier: "short" | "long") => { setFocused(null); setFocusedLibrary(focusedLibrary === tier ? null : tier); };
  const completed = ["generation", "completed"].includes(phase || "");
  const skipped = plan?.route === "none" && !steps.some((step) => /^(short|long)_retrieval$/.test(step.phase));
  const fusion = steps.find((step) => step.action === "deduplicate_scope_time_version");
  const selection = steps.find((step) => step.action === "dynamic_k_and_token_budget");
  const target = dynamicK?.target_k ?? selection?.detail.match(/(?:^|;)\s*target=(\d+)/)?.[1];
  const modeKnownDisabled = retrievalMode === "lexical_baseline";
  const forView = (view: View) => channels.filter((channel) => channel.view === view);
  const forTier = (tier: "short" | "long") => steps.find((step) => step.phase === `${tier}_retrieval`);
  const tierVisited = (tier: "short" | "long") => !!forTier(tier) || sources.some((source) => source.metadata.tier === tier) || channels.some((channel) => channel.tier === tier && channel.status === "complete");
  const tierStatus = (tier: "short" | "long") => {
    const step = forTier(tier);
    if (step) return `${step.input_count} 条输入 · ${step.output_count} 条召回`;
    const count = sources.filter((source) => source.metadata.tier === tier).length;
    if (count) return `${count} 条入选来源`;
    if (channels.some((channel) => channel.tier === tier && channel.status === "complete")) return completed ? "已检索 · 无入选来源" : "已执行检索";
    if (channels.some((channel) => channel.tier === tier) && channels.filter((channel) => channel.tier === tier).every((channel) => channel.status !== "complete")) return "本次未检索";
    return completed ? "未记录检索统计" : TIERS.find((item) => item.id === tier)!.note;
  };
  const status = (view: View) => {
    const records = forView(view);
    if (skipped) return "本轮无需检索";
    if (records.some((record) => record.status === "complete")) return `${records.reduce((sum, record) => sum + record.matched_count, 0)} 次命中`;
    const hits = hitsFor(sources, view).length;
    if (hits) return `${hits} 条入选证据`;
    if (view === "semantic" && (records.some((record) => record.status === "disabled") || (!records.length && modeKnownDisabled))) return "可选 · 未启用";
    if (records.length && records.every((record) => record.status === "skipped")) return "本次未触发";
    return completed ? "未记录通道统计" : "等待检索记录";
  };
  const state = (view: View) => {
    if (skipped) return "skipped";
    if (forView(view).some((record) => record.status === "complete")) return "complete";
    if (hitsFor(sources, view).length) return "complete";
    if (view === "semantic" && (forView(view).some((record) => record.status === "disabled") || (!forView(view).length && modeKnownDisabled))) return "disabled";
    if (forView(view).some((record) => record.status === "skipped")) return "skipped";
    return hitsFor(sources, view).length ? "complete" : "waiting";
  };
  const knownInteraction = (view: View, tier: "short" | "long") => state(view) !== "disabled" && !skipped && (channels.some((channel) => channel.view === view && channel.tier === tier && channel.status === "complete") || hitsFor(sources, view, tier).length > 0);
  const stageRunning = live && phase === "retrieval";
  const detail = VIEWS.find((view) => view.id === focused);
  const idleViews = VIEWS.filter((view) => ["disabled", "skipped"].includes(state(view.id)));
  const visibleViews = VIEWS.filter((view) => !idleViews.includes(view));
  const canvasHeight = visibleViews.length === 3 ? 300 : 220;
  const selectView = (view: View) => { setFocusedLibrary(null); setFocused(focused === view ? null : view); };

  if (skipped) return <section className="retrieval-routes routes-skipped" aria-label="三路检索与记忆库"><Search size={18} /><span>本轮无需检索记忆</span><ArrowRight size={17} /><span>直接组织回答</span><p>没有执行语义、词法或符号召回。</p></section>;

  return <section className={`retrieval-routes ${stageRunning ? "routes-live" : ""}`} aria-label="检索与记忆库">
    <div className="routes-heading"><strong>检索与记忆库</strong><span>{stageRunning ? replaying ? "正在回放已有召回记录" : "正在接收召回记录" : completed ? "本次实际检索" : "等待实际检索记录"}</span></div>
    {!!visibleViews.length && <div className="routes-canvas" style={{ "--route-height": `${canvasHeight}px` } as CSSProperties}>
      <svg className="routes-wires" viewBox={`0 0 1000 ${canvasHeight}`} preserveAspectRatio="none" aria-hidden="true">
        <defs>{VIEWS.map((view) => <marker key={view.id} id={`${id}-${view.id}`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path className={`route-arrow ${view.id}`} d="M 0 0 L 10 5 L 0 10 z" /></marker>)}</defs>
        {visibleViews.map((view, index) => {
          const y = 12 + (canvasHeight - 24) * (index + 0.5) / visibleViews.length;
          return <g key={view.id} className={`route-lines ${view.id} ${focused && focused !== view.id ? "route-muted" : ""}`}>
            <path className={`route-query-wire ${state(view.id) === "complete" ? "traversed" : ""}`} d={`M100 ${canvasHeight / 2} C180 ${canvasHeight / 2} 175 ${y} 220 ${y}`} />
            {TIERS.map((tier, tierIndex) => {
              const interacted = knownInteraction(view.id, tier.id);
              const bankY = canvasHeight / 2 + (tierIndex ? 54 : -54);
              return <path key={tier.id} data-view={view.id} data-tier={tier.id} className={`route-bank-wire ${interacted ? "traversed" : ""}`} d={`M500 ${y} C580 ${y} 607 ${bankY} 690 ${bankY}`} markerEnd={interacted ? `url(#${id}-${view.id})` : undefined} markerStart={interacted ? `url(#${id}-${view.id})` : undefined} />;
            })}
          </g>;
        })}
      </svg>
      <div className="route-query"><div><Search size={18} /></div><strong>查询计划</strong><span>{plan ? `${plan.required_info.length || 1} 个信息项` : "等待规划"}</span></div>
      <div className="route-channels">{visibleViews.map((view) => <button key={view.id} type="button" className={`route-channel ${view.id} ${state(view.id)} ${focused === view.id ? "selected" : ""}`} aria-expanded={focused === view.id} onClick={() => selectView(view.id)}>
        <view.icon size={18} /><span className="route-channel-copy"><strong>{view.name}</strong><small>{view.method}</small></span><span className="route-channel-status">{status(view.id)}</span>
      </button>)}</div>
      <div className="route-libraries">{TIERS.map((tier) => <button type="button" key={tier.id} aria-expanded={focusedLibrary === tier.id} aria-label={`查看${tier.name}本轮来源`} onClick={() => selectLibrary(tier.id)} className={`route-library ${tierVisited(tier.id) ? "visited" : ""} ${focusedLibrary === tier.id ? "selected" : ""}`}><div><Database size={17} /><strong>{tier.name}</strong></div><span>{tierStatus(tier.id)}</span></button>)}</div>
    </div>}
    {!visibleViews.length && <p className="routes-caption">本次没有执行检索通道匹配。</p>}
    <div className="routes-mobile-ledger" aria-label="分库检索记录">{TIERS.map((tier) => <button type="button" key={tier.id} aria-expanded={focusedLibrary === tier.id} aria-label={`查看${tier.name}本轮来源`} onClick={() => selectLibrary(tier.id)}><Database size={14} /><strong>{tier.name}</strong><span>{tierStatus(tier.id)}</span></button>)}</div>
    {!!idleViews.length && <details className="routes-idle" onToggle={(event) => { if (!event.currentTarget.open && idleViews.some((view) => view.id === focused)) setFocused(null); }}>
      <summary>其他通道状态 · {idleViews.length}</summary>
      <div>{idleViews.map((view) => <button type="button" key={view.id} className={`route-idle-channel ${view.id}`} aria-expanded={focused === view.id} onClick={() => selectView(view.id)}>
        <view.icon size={16} /><strong>{view.name}</strong><span>{status(view.id)}</span>
        <small>{forView(view.id).length ? [...new Set(forView(view.id).map(channelIdleReason))].join(" ") : "语义检索为可选增强，此次记录使用未配置向量服务的检索模式。"}</small>
      </button>)}</div>
    </details>}
    {detail && <div className={`route-detail ${detail.id}`}><div><detail.icon size={17} /><strong>{detail.name}</strong><span>{status(detail.id)}</span></div><details className="channel-method"><summary>这一路的匹配方法</summary><p>{detail.description}</p></details>{forView(detail.id).length ? <dl>{forView(detail.id).map((channel) => <div key={channel.tier}><dt>{channel.tier === "short" ? "短期库" : "长期库"}</dt><dd>{channel.status !== "complete" ? channelIdleReason(channel) : `${channel.input_count} 条检查 / ${channel.matched_count} 条命中${completed ? ` / ${channel.selected_count} 条最终入选` : ""}`}</dd></div>)}</dl> : <p className="route-detail-empty">本次记录未提供该通道的完整统计，不从最终证据反推候选数量。</p>}<ChannelContents key={detail.id} channels={forView(detail.id)} view={detail.id} sources={sources} completed={completed} onSource={onSource} /></div>}
    {focusedLibrary && <LibraryEvidence sources={sources} tier={focusedLibrary} onSource={onSource} />}
    <div className="route-return"><ArrowDown size={13} /><span>召回结果返回并汇合</span></div>
    <div className="route-finish">
      <div className={fusion ? "done" : ""}><Layers3 size={18} /><strong>融合与去重</strong><span>{fusion ? `${fusion.input_count} → ${fusion.output_count} 条` : "按 ID 与版本合并"}</span></div>
      <ArrowRight className="finish-arrow" size={16} />
      <div className={selection ? "done" : ""}><SlidersHorizontal size={18} /><strong>动态 K 与预算</strong><span>{selection ? `${target ? `目标 ${target} · ` : ""}入选 ${selection.output_count} 条` : "相关性与上下文约束"}</span></div>
      <ArrowRight className="finish-arrow" size={16} />
      <div className={completed || sources.length ? "done" : ""}><Layers3 size={18} /><strong>回答证据</strong><span>{completed || sources.length ? `${sources.length} 条可查看来源` : "等待入选证据"}</span></div>
    </div>
    {dynamicK && <div className="route-budget"><span>信息需求 <strong>{dynamicK.required_info_count}</strong> 项</span><span>规划深度 <strong>{dynamicK.planned_depth}</strong></span><span>候选上限 <strong>{dynamicK.candidate_limit}</strong></span><span>上下文 <strong>{dynamicK.used_tokens} / {dynamicK.token_limit}</strong> 估算 tokens</span></div>}
    <p className="routes-caption">{completed && !sources.length ? "本轮未找到可用的相关记忆，回答不引用记忆证据。" : "先检查短期记忆，必要时补查长期库。连线表示实际读库与返回，不代表三路并发执行。"}</p>
    {!!sources.length && <div className="routes-evidence">{sources.slice(0, 6).map((source, index) => <button type="button" key={`${source.chunk_id}:${index}`} onClick={() => onSource(index + 1)}><span className="route-source-number">{index + 1}</span><span className="route-source-content">{source.content}</span><span className="route-source-views">{VIEWS.filter((view) => hitsFor([source], view.id).length).map((view) => <i key={view.id} className={view.id} title={view.name} />)}</span></button>)}{sources.length > 6 && <small>其余 {sources.length - 6} 条可从回答引用查看。</small>}</div>}
  </section>;
}
