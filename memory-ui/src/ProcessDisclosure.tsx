import { useEffect, useState } from "react";
import { Check, ChevronDown, CircleDashed, Pause, Play, RotateCcw, Sparkles } from "lucide-react";
import type { AnswerContext, Hit, QueryStep } from "./types";
import type { RunEvent, RunMode } from "./RunCircuit";
import { readableStep } from "./TraceLanguage";
import { SourceDialog } from "./AnswerMessage";

const TITLES: Record<string, string> = { extraction: "正在整理新信息", planning: "正在理解问题", retrieval: "正在寻找相关记忆", generation: "正在组织回答", completed: "已完成回答" };
function MemoryFlow({ sources, phase, live, route, steps, onSource }: { sources: Hit[]; phase?: string; live: boolean; route?: string; steps: QueryStep[]; onSource: (number: number) => void }) {
  const short = sources.filter((source) => source.metadata.tier === "short").length;
  const long = sources.filter((source) => source.metadata.tier === "long").length;
  const searched = ["retrieval", "generation", "completed"].includes(phase || "");
  const shortSearched = steps.some((step) => step.phase === "short_retrieval") || short > 0;
  const longSearched = steps.some((step) => step.phase === "long_retrieval") || long > 0;
  const done = ["generation", "completed"].includes(phase || "");
  const skipped = route === "none" && !shortSearched && !longSearched;
  const generated = ["generation", "completed"].includes(phase || "");
  return <div className={`memory-flow ${live ? "is-live" : ""}`}>
    <svg viewBox="0 0 620 164" role="img" aria-label={`记忆汇流图：${short} 条短期证据，${long} 条长期证据`}>
      {skipped ? <path className={`flow-wire ${generated ? "lit" : ""}`} d="M89 82 H534" /> : <>
      <path className={`flow-wire ${shortSearched ? "lit" : ""}`} d="M89 82 C150 82 145 38 207 38" />
      <path className={`flow-wire ${longSearched ? "lit" : ""}`} d="M89 82 C150 82 145 126 207 126" />
      <path className={`flow-wire ${short ? "lit" : ""}`} d="M313 38 C365 38 345 82 399 82" />
      <path className={`flow-wire ${long ? "lit" : ""}`} d="M313 126 C365 126 345 82 399 82" />
      <path className={`flow-wire ${generated ? "lit" : ""}`} d="M441 82 H534" />
      </>}
      <circle className="flow-node query" cx="67" cy="82" r="22" /><text x="67" y="87" textAnchor="middle">问</text><text className="flow-label" x="67" y="127" textAnchor="middle">理解问题</text>
      {!skipped && <><rect className={`flow-pool ${short ? "has-source" : ""}`} x="207" y="18" width="106" height="40" rx="20" /><text x="260" y="43" textAnchor="middle">短期记忆{done || sources.length ? ` · ${short}` : ""}</text>
      <rect className={`flow-pool ${long ? "has-source" : ""}`} x="207" y="106" width="106" height="40" rx="20" /><text x="260" y="131" textAnchor="middle">长期记忆{done || sources.length ? ` · ${long}` : ""}</text>
      <circle className={`flow-node ${sources.length ? "evidence" : ""}`} cx="420" cy="82" r="23" /><text x="420" y="87" textAnchor="middle">{done ? sources.length : sources.length || "·"}</text><text className="flow-label" x="420" y="127" textAnchor="middle">入选证据</text></>}
      <circle className={`flow-node ${generated ? "answer" : ""}`} cx="556" cy="82" r="22" /><path className="answer-glyph" d="M547 77 H565 M547 83 H565 M547 89 H559" /><text className="flow-label" x="556" y="127" textAnchor="middle">组织回复</text>
    </svg>
    <p className="flow-caption">{sources.length ? "从相关记忆中选取证据，按当前问题组织回答。" : skipped ? "本轮无需检索记忆，直接组织回答。" : done ? "本轮未找到可用的相关记忆，回答不会引用记忆证据。" : searched ? "已进入检索阶段，等待返回证据。" : route ? "查询范围已确定，等待检索返回；连线按实际执行记录点亮。" : "等待查询规划；连线按实际执行记录点亮。"}</p>
    {!!sources.length && <div className="flow-evidence">{sources.slice(0, 6).map((source, index) => <button key={`${source.chunk_id}:${index}`} onClick={() => onSource(index + 1)}><span>{index + 1}</span>{source.content}</button>)}{sources.length > 6 && <small>另有 {sources.length - 6} 条来源，可从回答引用查看。</small>}</div>}
  </div>;
}
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
      <MemoryFlow sources={sources} phase={latest?.phase || (context ? "completed" : undefined)} live={live || playing} route={plan?.route} steps={querySteps} onSource={setSource} />
      {!!querySteps.length && <details className="process-decisions"><summary>检索做了哪些取舍 · {querySteps.length}</summary><ol>{querySteps.map((step, index) => { const readable = readableStep(step, { plan: plan || null, retrieval_mode: context?.retrieval_mode || "" }); return <li key={index}><strong>{readable.title}</strong><p>{readable.detail}</p></li>; })}</ol></details>}
      {!!shown.length && <ol className="process-events" aria-label="服务阶段时间线">{shown.map((event, index) => <li key={index} className={index === shown.length - 1 && live ? "current" : ""}><i /><div><span>{event.detail}</span><time>{(event.elapsed_ms / 1000).toFixed(2)}s</time></div></li>)}</ol>}
      {!live && context && <p className="process-summary">{context.candidate_count} 条候选 → {context.selected_k} 条入选证据{context.acceleration?.avoided_model_calls ? ` · 复用已有回答，节省 ${context.acceleration.avoided_model_calls} 次生成` : ""}</p>}
      <small className="process-footnote">查询计划、检索结果与服务阶段的可验证记录。</small>
    </div>}
    {source !== null && <SourceDialog source={sources[source - 1]} number={source} available={true} onClose={() => setSource(null)} />}
  </div>;
}
