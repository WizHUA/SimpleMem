import {
  ArrowUpToLine,
  BrainCircuit,
  Database,
  GitBranch,
  LoaderCircle,
  MessageSquareText,
  Plus,
  RefreshCw,
  Route,
  Send,
  Sparkles,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, api } from "./api";
import type { AnswerResult, Health, Hierarchy, Memory, SessionSnapshot } from "./types";

type InspectorTab = "short" | "long" | "trace";

const EMPTY_HIERARCHY: Hierarchy = {
  schema: "hmem-reference/v1",
  organization_mode: "domain_category_trace_episode",
  summary_mode: "labels_from_memory_extraction",
  domain_count: 0,
  episode_count: 0,
  domains: [],
};

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function roleName(role: string) {
  return role === "user" ? "用户" : role === "assistant" ? "GLM" : "工具";
}

function ShortMemoryPanel({
  snapshot,
  memories,
  onExtract,
  onPromote,
  busy,
}: {
  snapshot: SessionSnapshot | null;
  memories: Memory[];
  onExtract: () => void;
  onPromote: (memory: Memory) => void;
  busy: string;
}) {
  const short = memories.filter(
    (memory) =>
      memory.tier === "short" &&
      memory.status === "active" &&
      (!snapshot || memory.session_id === snapshot.session.session_id),
  );
  return (
    <div className="inspector-content">
      <div className="section-heading">
        <div>
          <h2>短期记忆</h2>
          <span>{snapshot?.pending_turns ?? 0} 条待整理事件</span>
        </div>
        <button
          className="command-button"
          type="button"
          onClick={onExtract}
          disabled={!snapshot || snapshot.pending_turns === 0 || Boolean(busy)}
        >
          <Sparkles size={15} aria-hidden="true" />
          整理
        </button>
      </div>

      <section className="plain-section">
        <h3>大模型摘要</h3>
        <p className={snapshot?.session.summary ? "summary-text" : "empty-text"}>
          {snapshot?.session.summary || "暂无摘要"}
        </p>
      </section>

      <section className="plain-section state-grid">
        <div>
          <h3>当前目标</h3>
          <p>{snapshot?.session.goal || "未设置"}</p>
        </div>
        <div>
          <h3>计划与待办</h3>
          <p>
            {[...(snapshot?.session.plan || []), ...(snapshot?.session.pending_tasks || [])].join("；") || "无"}
          </p>
        </div>
      </section>

      <section className="plain-section">
        <h3>结构化短期记忆</h3>
        <div className="entry-list">
          {short.length === 0 && <p className="empty-text">暂无结构化记忆</p>}
          {short.map((memory) => (
            <article className="memory-entry" key={`${memory.memory_id}:${memory.version}`}>
              <div className="entry-meta">
                <span>{memory.kind}</span>
                <span>{memory.scope_type}</span>
                <span>{memory.assertion}</span>
              </div>
              <p>{memory.content}</p>
              <div className="entry-footer">
                <span>{memory.evidence.length} 条证据</span>
                {memory.durable && memory.scope_type !== "session" && (
                  <button
                    className="text-command"
                    type="button"
                    onClick={() => onPromote(memory)}
                    disabled={Boolean(busy)}
                  >
                    <ArrowUpToLine size={14} aria-hidden="true" />
                    晋升长期
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="plain-section">
        <h3>近期原始对话</h3>
        <div className="raw-list">
          {(snapshot?.recent_turns || []).flatMap((turn) =>
            turn.events.map((event, index) => (
              <div className="raw-row" key={`${turn.turn_id}:${index}`}>
                <span>{roleName(event.role)}</span>
                <p>{event.content}</p>
              </div>
            )),
          )}
          {!snapshot?.recent_turns.length && <p className="empty-text">暂无对话</p>}
        </div>
      </section>
    </div>
  );
}

function HierarchyPanel({ hierarchy }: { hierarchy: Hierarchy }) {
  return (
    <div className="inspector-content">
      <div className="section-heading">
        <div>
          <h2>H-MEM 长期组织</h2>
          <span>
            {hierarchy.domain_count} 个领域 · {hierarchy.episode_count} 个版本
          </span>
        </div>
      </div>
      {hierarchy.domains.length === 0 && <p className="empty-block">暂无长期记忆</p>}
      <div className="tree">
        {hierarchy.domains.map((domain) => (
          <details open key={domain.name}>
            <summary>
              <Database size={15} aria-hidden="true" />
              <strong>{domain.name}</strong>
              <span>{domain.episode_count}</span>
            </summary>
            {domain.categories.map((category) => (
              <details open className="tree-category" key={`${domain.name}:${category.name}`}>
                <summary>
                  <GitBranch size={14} aria-hidden="true" />
                  {category.name}
                  <span>{category.episode_count}</span>
                </summary>
                {category.traces.map((trace) => (
                  <div className="trace-group" key={`${category.name}:${trace.name}`}>
                    <div className="trace-title">
                      <span>{trace.name}</span>
                      <span>{trace.active_count}/{trace.episode_count} 有效</span>
                    </div>
                    {trace.episodes.map((episode) => (
                      <article className="episode" key={`${episode.memory_id}:${episode.version}`}>
                        <div className="entry-meta">
                          <span>v{episode.version}</span>
                          <span className={`status-${episode.status}`}>{episode.status}</span>
                          <span>{episode.scope_type}</span>
                        </div>
                        <p>{episode.content}</p>
                        <span className="episode-source">{episode.evidence_count} 条证据</span>
                      </article>
                    ))}
                  </div>
                ))}
              </details>
            ))}
          </details>
        ))}
      </div>
    </div>
  );
}

function TracePanel({ trace }: { trace: AnswerResult | null }) {
  if (!trace?.plan) return <p className="empty-block">完成一次对话后显示本轮查询计划</p>;
  const plan = trace.plan;
  return (
    <div className="inspector-content">
      <div className="section-heading">
        <div>
          <h2>意图与查询</h2>
          <span>{trace.elapsed_ms} ms · {trace.retrieval_mode}</span>
        </div>
      </div>
      <section className="plan-overview">
        <div><span>路由</span><strong>{plan.route}</strong></div>
        <div><span>动态 K</span><strong>{trace.selected_k}/{plan.depth}</strong></div>
        <div><span>候选</span><strong>{trace.candidate_count}</strong></div>
        <div><span>上下文</span><strong>{trace.context_tokens}</strong></div>
      </section>
      <section className="plain-section">
        <h3>语义查询</h3>
        <ol className="query-list">
          {plan.semantic_queries.map((query) => <li key={query}>{query}</li>)}
        </ol>
        {plan.keywords.length > 0 && <div className="keyword-row">{plan.keywords.map((word) => <span key={word}>{word}</span>)}</div>}
      </section>
      <section className="plain-section">
        <h3>必需信息</h3>
        <ul className="query-list">
          {plan.required_info.map((item) => <li key={item}>{item}</li>)}
          {plan.required_info.length === 0 && <li>未指定</li>}
        </ul>
      </section>
      <section className="plain-section">
        <h3>实际查询步骤</h3>
        <ol className="step-list">
          {trace.query_steps.map((step) => (
            <li key={step.order}>
              <span>{step.order}</span>
              <div><strong>{step.action}</strong><p>{step.detail}</p></div>
              <em>{step.input_count} → {step.output_count}</em>
            </li>
          ))}
        </ol>
      </section>
      <section className="plain-section">
        <h3>检索来源</h3>
        <div className="source-list">
          {trace.sources.map((source) => (
            <div key={source.chunk_id}>
              <span>{source.score.toFixed(3)}</span>
              <p>{source.content}</p>
            </div>
          ))}
          {trace.sources.length === 0 && <p className="empty-text">本轮未检索到来源</p>}
        </div>
      </section>
    </div>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [snapshot, setSnapshot] = useState<SessionSnapshot | null>(null);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [hierarchy, setHierarchy] = useState<Hierarchy>(EMPTY_HIERARCHY);
  const [trace, setTrace] = useState<AnswerResult | null>(null);
  const [tab, setTab] = useState<InspectorTab>("short");
  const [input, setInput] = useState("");
  const [topK, setTopK] = useState(5);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const feedRef = useRef<HTMLDivElement>(null);

  const messages = useMemo(
    () => snapshot?.recent_turns.flatMap((turn) => turn.events.map((event, index) => ({
      ...event, id: `${turn.turn_id}:${index}`,
    }))) || [],
    [snapshot],
  );

  async function refresh(sessionId = snapshot?.session.session_id) {
    if (!sessionId) return;
    const [nextSnapshot, nextMemories, nextHierarchy] = await Promise.all([
      api.session(sessionId), api.memories(), api.hierarchy(),
    ]);
    setSnapshot(nextSnapshot);
    setMemories(nextMemories);
    setHierarchy(nextHierarchy);
  }

  async function createSession() {
    const session = await api.createSession("长短期记忆验证", "memory-system-demo");
    localStorage.setItem("memory-session-id", session.session_id);
    setTrace(null);
    await refresh(session.session_id);
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setBusy("初始化");
      try {
        const modelHealth = await api.health();
        if (cancelled) return;
        setHealth(modelHealth);
        const saved = localStorage.getItem("memory-session-id");
        if (saved) {
          try {
            await refresh(saved);
          } catch (cause) {
            if (!(cause instanceof ApiError) || cause.status !== 404) throw cause;
            await createSession();
          }
        } else {
          await createSession();
        }
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "无法连接后端");
      } finally {
        if (!cancelled) setBusy("");
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, busy]);

  async function handleSend(event: FormEvent) {
    event.preventDefault();
    const query = input.trim();
    if (!query || !snapshot || busy) return;
    setBusy("GLM 正在回答");
    setError("");
    try {
      await api.append(snapshot.session.session_id, "user", query);
      setInput("");
      await refresh(snapshot.session.session_id);
      const result = await api.answer(snapshot.session.session_id, query, topK);
      setTrace(result);
      setTab("trace");
      await api.append(snapshot.session.session_id, "assistant", result.generated_text);
      await refresh(snapshot.session.session_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "对话失败");
      await refresh(snapshot.session.session_id).catch(() => undefined);
    } finally {
      setBusy("");
    }
  }

  async function handleExtract() {
    if (!snapshot || busy) return;
    setBusy("整理短期记忆");
    setError("");
    try {
      await api.extract(snapshot.session.session_id);
      await refresh(snapshot.session.session_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "整理失败");
    } finally {
      setBusy("");
    }
  }

  async function handlePromote(memory: Memory) {
    if (busy) return;
    setBusy("晋升长期记忆");
    setError("");
    try {
      await api.promote(memory);
      await refresh(snapshot?.session.session_id);
      setTab("long");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "晋升失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="brand"><BrainCircuit size={21} aria-hidden="true" /><strong>记忆验证台</strong></div>
        <div className="model-status">
          <span className={health?.status === "ok" ? "status-dot ok" : "status-dot"} />
          <span>{health?.model_name || "模型未配置"}</span>
          {health?.model_provider && <em>{health.model_provider}</em>}
        </div>
        <div className="header-actions">
          <button className="icon-button" type="button" title="刷新后端数据" aria-label="刷新后端数据" onClick={() => refresh()} disabled={Boolean(busy)}>
            <RefreshCw size={17} aria-hidden="true" />
          </button>
          <button className="command-button" type="button" onClick={createSession} disabled={Boolean(busy)}>
            <Plus size={16} aria-hidden="true" />新会话
          </button>
        </div>
      </header>

      <main className="workspace">
        <section className="conversation" aria-label="对话">
          <div className="conversation-header">
            <div>
              <h1>{snapshot?.session.topic || "正在连接后端"}</h1>
              <span>{snapshot?.session.session_id || ""}</span>
            </div>
            <label className="top-k-control">Top K
              <input type="number" min="1" max="20" value={topK} onChange={(event) => setTopK(Math.max(1, Math.min(20, Number(event.target.value))))} />
            </label>
          </div>

          <div className="message-feed" ref={feedRef} aria-live="polite">
            {messages.length === 0 && <div className="chat-empty"><MessageSquareText size={28} aria-hidden="true" /><p>开始一次对话</p></div>}
            {messages.map((message) => (
              <article className={`message message-${message.role}`} key={message.id}>
                <div><strong>{roleName(message.role)}</strong><time>{formatTime(message.occurred_at)}</time></div>
                <p>{message.content}</p>
              </article>
            ))}
            {busy === "GLM 正在回答" && <div className="thinking"><LoaderCircle size={16} className="spin" aria-hidden="true" />GLM 正在回答</div>}
          </div>

          {error && <div className="error-bar" role="alert">{error}</div>}
          <form className="composer" onSubmit={handleSend}>
            <textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); }
            }} placeholder="输入消息" rows={3} disabled={!snapshot || Boolean(busy)} />
            <button className="send-button" type="submit" title="发送" aria-label="发送" disabled={!input.trim() || !snapshot || Boolean(busy)}>
              <Send size={18} aria-hidden="true" />
            </button>
          </form>
        </section>

        <aside className="inspector" aria-label="记忆与查询检查器">
          <nav className="tabs" aria-label="检查器视图">
            <button type="button" className={tab === "short" ? "active" : ""} onClick={() => setTab("short")}><BrainCircuit size={15} aria-hidden="true" />短期</button>
            <button type="button" className={tab === "long" ? "active" : ""} onClick={() => setTab("long")}><Database size={15} aria-hidden="true" />长期</button>
            <button type="button" className={tab === "trace" ? "active" : ""} onClick={() => setTab("trace")}><Route size={15} aria-hidden="true" />查询</button>
          </nav>
          {tab === "short" && <ShortMemoryPanel snapshot={snapshot} memories={memories} onExtract={handleExtract} onPromote={handlePromote} busy={busy} />}
          {tab === "long" && <HierarchyPanel hierarchy={hierarchy} />}
          {tab === "trace" && <TracePanel trace={trace} />}
        </aside>
      </main>
    </div>
  );
}
