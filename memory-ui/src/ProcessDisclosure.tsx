import { useEffect, useState } from "react";
import { Check, ChevronDown, CircleDashed, Pause, Play, RotateCcw, Sparkles } from "lucide-react";
import type { AnswerContext } from "./types";
import type { RunEvent, RunMode } from "./RunCircuit";
import { readableStep } from "./TraceLanguage";
import { SourceDialog } from "./AnswerMessage";
import { RetrievalRoutes } from "./RetrievalRoutes";

const TITLES: Record<string, string> = { extraction: "正在整理新信息", planning: "正在理解问题", retrieval: "正在寻找相关记忆", generation: "正在组织回答", completed: "已完成回答" };
export function ProcessDisclosure({ context, events = [], mode = "recorded" }: { context?: AnswerContext | null; events?: RunEvent[]; mode?: RunMode }) {
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [source, setSource] = useState<number | null>(null);
  const records = events.length ? events : context?.run_events || [];
  useEffect(() => { if (mode === "live") { setCursor(null); setPlaying(false); } }, [mode]);
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setCursor((previous) => { const next = (previous ?? -1) + 1; if (next >= records.length - 1) setPlaying(false); return Math.min(next, records.length - 1); }), 900);
    return () => window.clearInterval(timer);
  }, [playing, records.length]);
  const replaying = cursor !== null;
  const shown = replaying ? records.slice(0, cursor + 1) : records;
  const latest = shown.at(-1);
  const plan = [...shown].reverse().find((event) => event.plan)?.plan || (!replaying ? context?.plan : null);
  const sources = [...shown].reverse().find((event) => event.sources)?.sources || (!replaying ? context?.sources : []) || [];
  const querySteps = [...shown].reverse().find((event) => event.steps)?.steps || (!replaying ? context?.query_steps : []) || [];
  const channels = (!replaying && context?.channels) || [...shown].reverse().find((event) => event.channels)?.channels || [];
  const dynamicK = (!replaying && context?.dynamic_k) || [...shown].reverse().find((event) => event.dynamic_k)?.dynamic_k || null;
  const live = mode === "live";
  const title = replaying ? "记录回放 · 节奏已压缩" : live ? TITLES[latest?.phase || "planning"] || "正在处理" : mode === "error" ? "运行中断 · 可查看已完成步骤" : "已完成检索与回答";
  const [liveElapsed, setLiveElapsed] = useState(0);
  useEffect(() => {
    if (!live) return;
    const started = performance.now();
    const base = latest?.elapsed_ms || 0;
    setLiveElapsed(base);
    const clock = window.setInterval(() => setLiveElapsed(base + performance.now() - started), 250);
    return () => window.clearInterval(clock);
  }, [live, latest?.elapsed_ms]);
  const elapsed = live ? Math.max(latest?.elapsed_ms || 0, liveElapsed) : latest?.elapsed_ms ?? context?.elapsed_ms;
  return <div className={`process-disclosure ${live ? "processing" : ""} ${mode === "error" ? "interrupted" : ""}`}>
    <button type="button" className="process-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
      {live ? <Sparkles className="process-spark" size={15} /> : mode === "error" ? <CircleDashed size={15} /> : <Check size={15} />}<span className="process-title">{title}</span>{elapsed !== undefined && <time>{(elapsed / 1000).toFixed(1)} 秒</time>}<ChevronDown size={13} className={open ? "turned" : ""} />
    </button>
    {live && !open && <p className="live-detail" aria-live="polite">{latest?.detail || "正在连接记忆服务…"}</p>}
    {open && <div className="process-body">
      <div className="process-toolbar"><span>{replaying ? "正在回放已发生的服务记录" : live ? "实时服务记录" : "实际运行记录"}</span>{!live && !!records.length && <div><button aria-label={playing ? "暂停记录回放" : "回放运行记录"} onClick={() => { if (playing) setPlaying(false); else { setCursor(-1); setPlaying(true); } }}>{playing ? <Pause size={12} /> : <Play size={12} />}{playing ? "暂停" : "回放"}</button>{replaying && <button aria-label="退出记录回放" onClick={() => { setCursor(null); setPlaying(false); }}><RotateCcw size={12} />结束回放</button>}</div>}</div>
      {plan && <div className="query-intent"><span>查找</span><strong>{plan.required_info.join("、") || [plan.subject, plan.predicate].filter(Boolean).join(" · ") || "与问题相关的信息"}</strong><small>{{ both: "短期 + 长期", short: "短期记忆", long: "长期记忆", none: "无需记忆检索" }[plan.route]}{plan.temporal_mode === "history" ? " · 历史信息" : " · 当前信息"}</small>{!!plan.semantic_queries.length && <p>{plan.semantic_queries.join("；")}</p>}</div>}
      <RetrievalRoutes sources={sources} phase={latest?.phase || (!replaying && context ? "completed" : undefined)} live={live || playing} replaying={replaying} plan={plan} steps={querySteps} channels={channels} dynamicK={dynamicK} retrievalMode={!replaying ? context?.retrieval_mode : undefined} onSource={setSource} />
      {!!querySteps.length && <details className="process-decisions"><summary>检索做了哪些取舍 · {querySteps.length}</summary><ol>{querySteps.map((step, index) => { const readable = readableStep(step, { plan: plan || null, retrieval_mode: context?.retrieval_mode || "" }); return <li key={index}><strong>{readable.title}</strong><p>{readable.detail}</p></li>; })}</ol></details>}
      {!!shown.length && <ol className="process-events" aria-label="服务阶段时间线">{shown.map((event, index) => <li key={index} className={index === shown.length - 1 && live ? "current" : ""}><i /><div><span>{event.detail}</span><time>{(event.elapsed_ms / 1000).toFixed(2)}s</time></div></li>)}</ol>}
      {!live && context && <p className="process-summary">{context.candidate_count} 条候选 → {context.selected_k} 条入选证据{context.acceleration?.avoided_model_calls ? ` · 复用已有回答，节省 ${context.acceleration.avoided_model_calls} 次生成` : ""}</p>}
      <small className="process-footnote">查询计划、检索结果与服务阶段的可验证记录。</small>
    </div>}
    {source !== null && <SourceDialog source={sources[source - 1]} number={source} available={true} onClose={() => setSource(null)} />}
  </div>;
}
