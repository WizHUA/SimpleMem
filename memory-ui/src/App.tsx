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
  Activity,
  ShieldCheck,
  X,
  ArrowRight,
  FlaskConical,
  Settings2,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  ApiError,
  api,
  apiBase,
  getServiceToken,
  setServiceToken,
} from "./api";
import {
  ConnectionDialog,
  EvolutionDialog,
  HistoryDialog,
} from "./MemoryDialogs";
import type {
  AnswerResult,
  EvolutionInput,
  Health,
  Hierarchy,
  HistoryEntry,
  Memory,
  SessionSnapshot,
} from "./types";

type InspectorTab = "short" | "long" | "trace" | "observe";

const EMPTY_HIERARCHY: Hierarchy = {
  schema: "hmem-reference/v1",
  organization_mode: "domain_category_trace_episode",
  summary_mode: "labels_from_memory_extraction",
  domain_count: 0,
  episode_count: 0,
  domains: [],
};

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function roleName(role: string) {
  return role === "user" ? "用户" : role === "assistant" ? "助手" : "工具";
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
      ["active", "pending"].includes(memory.status) &&
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
        <p
          className={snapshot?.session.summary ? "summary-text" : "empty-text"}
        >
          {snapshot?.session.summary ||
            "发送包含明确事实的消息后，点击“整理”生成摘要。"}
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
            {[
              ...(snapshot?.session.plan || []),
              ...(snapshot?.session.pending_tasks || []),
            ].join("；") || "无"}
          </p>
        </div>
      </section>

      <section className="plain-section">
        <h3>结构化短期记忆</h3>
        <div className="entry-list">
          {short.length === 0 && (
            <p className="empty-text">
              尚未提取记忆。整理会将对话转换为带原文证据的事实、偏好与事件。
            </p>
          )}
          {short.map((memory) => (
            <article
              className="memory-entry"
              key={`${memory.memory_id}:${memory.version}`}
            >
              <div className="entry-meta">
                <span>{label(memory.kind)}</span>
                <span>{label(memory.scope_type)}作用域</span>
                <span>{memory.assertion}</span>
                <span>{label(memory.status)}</span>
              </div>
              <p>{memory.content}</p>
              <details className="evidence-detail">
                <summary>
                  {memory.evidence.length} 条原文证据 · v{memory.version}
                </summary>
                {memory.evidence.map((item, index) => (
                  <blockquote key={`${item.turn_id}:${index}`}>
                    <p>{item.quote}</p>
                    <cite>
                      事件 {item.turn_id.slice(0, 12)} / 第{" "}
                      {item.event_index + 1} 条消息
                    </cite>
                  </blockquote>
                ))}
                <dl>
                  <dt>主语 / 关系 / 值</dt>
                  <dd>
                    {memory.subject} / {memory.predicate} / {memory.value}
                  </dd>
                  <dt>长期资格</dt>
                  <dd>
                    {memory.durable ? "稳定信息" : "临时信息"}，
                    {label(memory.scope_type)}作用域
                  </dd>
                </dl>
              </details>
              <div className="entry-footer">
                <span>{memory.evidence.length} 条证据</span>
                <button
                  className="text-command"
                  onClick={() => onPromote(memory)}
                  disabled={Boolean(busy)}
                >
                  审阅演化
                </button>
                {memory.status === "active" &&
                  memory.durable &&
                  memory.scope_type !== "session" &&
                  !["hypothetical", "inferred"].includes(memory.assertion) && (
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
          {!snapshot?.recent_turns.length && (
            <p className="empty-text">暂无对话</p>
          )}
        </div>
      </section>
    </div>
  );
}

function HierarchyPanel({
  hierarchy,
  memories,
  busy,
  onHistory,
  onEvolve,
}: {
  hierarchy: Hierarchy;
  memories: Memory[];
  busy: boolean;
  onHistory: (id: string) => void;
  onEvolve: (memory: Memory) => void;
}) {
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
      <p className="panel-intro">
        领域 → 类别 → 线索 →
        记忆版本。相同事实保留演化历史，检索按作用域与有效时间选择版本。此处展示当前用户可见的长期目录。
      </p>
      {hierarchy.domains.length === 0 && (
        <p className="empty-block">
          还没有长期记忆。先整理对话，再将稳定的用户或项目记忆晋升到这里。
        </p>
      )}
      {memories.some(
        (memory) => memory.tier === "long" && memory.status !== "active",
      ) && (
        <details className="archived-memories">
          <summary>非有效长期记忆与历史</summary>
          {memories
            .filter(
              (memory) => memory.tier === "long" && memory.status !== "active",
            )
            .map((memory) => (
              <article
                className="memory-entry"
                key={`${memory.memory_id}:${memory.version}`}
              >
                <p>{memory.content}</p>
                <span>
                  {label(memory.status)} · v{memory.version}
                </span>
                <button
                  className="text-command"
                  disabled={busy}
                  onClick={() => onHistory(memory.memory_id)}
                >
                  查看演化历史
                </button>
                {memory.status === "pending" && (
                  <button
                    className="text-command"
                    disabled={busy}
                    onClick={() => onEvolve(memory)}
                  >
                    审阅演化
                  </button>
                )}
              </article>
            ))}
        </details>
      )}
      <div className="tree">
        {hierarchy.domains.map((domain) => (
          <details open key={domain.name}>
            <summary>
              <Database size={15} aria-hidden="true" />
              <strong>{domain.name}</strong>
              <span>{domain.episode_count}</span>
            </summary>
            {domain.categories.map((category) => (
              <details
                open
                className="tree-category"
                key={`${domain.name}:${category.name}`}
              >
                <summary>
                  <GitBranch size={14} aria-hidden="true" />
                  {category.name}
                  <span>{category.episode_count}</span>
                </summary>
                {category.traces.map((trace) => (
                  <div
                    className="trace-group"
                    key={`${category.name}:${trace.name}`}
                  >
                    <div className="trace-title">
                      <span>{trace.name}</span>
                      <span>
                        {trace.active_count}/{trace.episode_count} 有效
                      </span>
                    </div>
                    {trace.episodes.map((episode) => (
                      <article
                        className="episode"
                        key={`${episode.memory_id}:${episode.version}`}
                      >
                        <div className="entry-meta">
                          <span>v{episode.version}</span>
                          <span className={`status-${episode.status}`}>
                            {label(episode.status)}
                          </span>
                          <span>{label(episode.scope_type)}</span>
                        </div>
                        <p>{episode.content}</p>
                        <span className="episode-source">
                          {episode.evidence_count} 条证据
                        </span>
                        <div className="entry-footer">
                          <button
                            className="text-command"
                            disabled={busy}
                            onClick={() => onHistory(episode.memory_id)}
                          >
                            查看演化历史
                          </button>
                          {memories.find(
                            (memory) =>
                              memory.memory_id === episode.memory_id &&
                              memory.version === episode.version,
                          ) && (
                            <button
                              className="text-command"
                              disabled={busy}
                              onClick={() =>
                                onEvolve(
                                  memories.find(
                                    (memory) =>
                                      memory.memory_id === episode.memory_id &&
                                      memory.version === episode.version,
                                  )!,
                                )
                              }
                            >
                              审阅 / 撤回
                            </button>
                          )}
                        </div>
                        <details className="technical-detail">
                          <summary>来源与有效时间</summary>
                          <p>来源会话：{episode.source_session_id}</p>
                          <p>作用域：{episode.scope_id}</p>
                          <p>
                            有效期：{episode.valid_from || "未限定"} 至{" "}
                            {episode.valid_to || "持续有效"}
                          </p>
                          <p>记录于：{episode.recorded_at}</p>
                        </details>
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
  if (!trace?.plan)
    return (
      <p className="empty-block">
        完成一次提问后，在这里查看路由决策、实际检索步骤和回答来源。
      </p>
    );
  const plan = trace.plan;
  return (
    <div className="inspector-content">
      <div className="section-heading">
        <div>
          <h2>意图与查询</h2>
          <span>
            {trace.elapsed_ms} ms · {trace.retrieval_mode}
          </span>
        </div>
      </div>
      <section className="plan-overview">
        <div>
          <span>路由</span>
          <strong>{label(plan.route)}</strong>
        </div>
        <div>
          <span>入选 K / 规划深度</span>
          <strong>
            {trace.selected_k} / {plan.depth}
          </strong>
        </div>
        <div>
          <span>候选</span>
          <strong>{trace.candidate_count}</strong>
        </div>
        <div>
          <span>上下文估算 tokens</span>
          <strong>{trace.context_tokens}</strong>
        </div>
      </section>
      {trace.warnings.length > 0 && (
        <div className="trace-warnings" role="status">
          {trace.warnings.map((warning, index) => (
            <p key={index}>{warning}</p>
          ))}
        </div>
      )}
      <section className="plain-section">
        <h3>语义查询</h3>
        <ol className="query-list">
          {plan.semantic_queries.map((query) => (
            <li key={query}>{query}</li>
          ))}
        </ol>
        {plan.keywords.length > 0 && (
          <div className="keyword-row">
            {plan.keywords.map((word) => (
              <span key={word}>{word}</span>
            ))}
          </div>
        )}
      </section>
      <section className="plain-section">
        <h3>必需信息</h3>
        <ul className="query-list">
          {plan.required_info.map((item) => (
            <li key={item}>{item}</li>
          ))}
          {plan.required_info.length === 0 && <li>未指定</li>}
        </ul>
      </section>
      <section className="plain-section">
        <h3>实际查询步骤</h3>
        <ol className="step-list">
          {trace.query_steps.map((step) => (
            <li key={step.order}>
              <span>{step.order}</span>
              <div>
                <strong>{step.action}</strong>
                <p>{step.detail}</p>
              </div>
              <em>
                {step.input_count} → {step.output_count}
              </em>
            </li>
          ))}
        </ol>
      </section>
      <section className="plain-section">
        <h3>检索来源</h3>
        <div className="source-list">
          {trace.sources.map((source, index) => (
            <div key={`${source.chunk_id}:${index}`}>
              <span>
                来源 {index + 1}
                <br />
                {source.score.toFixed(3)}
              </span>
              <div>
                <p>{source.content}</p>
                <details className="technical-detail">
                  <summary>
                    来源标识与元数据
                    {trace.citations.includes(index + 1) ? " · 已引用" : ""}
                  </summary>
                  <p>
                    {source.engine} / {source.source_file}
                  </p>
                  <pre>{JSON.stringify(source.metadata, null, 2)}</pre>
                </details>
              </div>
            </div>
          ))}
          {trace.sources.length === 0 && (
            <p className="empty-text">本轮未检索到来源</p>
          )}
        </div>
      </section>
    </div>
  );
}

const LABELS: Record<string, string> = {
  fact: "事实",
  preference: "偏好",
  event: "事件",
  procedure: "流程",
  user: "用户",
  project: "项目",
  session: "会话",
  active: "有效",
  superseded: "已被更新",
  retracted: "已撤销",
  pending: "待确认",
  none: "无需检索",
  short: "短期",
  long: "长期",
  both: "长短期联合",
};
function label(value: string) {
  return LABELS[value] || value;
}
function errorMessage(cause: unknown) {
  if (cause instanceof TypeError)
    return `无法连接记忆服务。请确认后端已启动，且允许当前网页来源访问（${apiBase}）。`;
  if (cause instanceof DOMException && cause.name === "TimeoutError")
    return "请求超时。已写入的对话会保留，请重试回答或刷新数据。";
  return cause instanceof Error ? cause.message : "操作未完成，请重试。";
}
function duration(ms: number) {
  return `${ms.toLocaleString("zh-CN", { maximumFractionDigits: 1 })} ms`;
}

function ObservePanel({
  trace,
  baseline,
  query,
  busy,
  onRun,
}: {
  trace: AnswerResult | null;
  baseline: AnswerResult | null;
  query: string;
  busy: string;
  onRun: (accelerate: boolean) => void;
}) {
  const stats = trace?.acceleration;
  const status = {
    disabled: "已关闭",
    miss: "首次生成 / 未命中",
    hit: "命中精确上下文",
    shared: "复用并发生成",
  };
  return (
    <div className="inspector-content">
      <div className="section-heading">
        <div>
          <h2>推理加速实验</h2>
          <span>逐请求实测 · 同一问题、同一上下文</span>
        </div>
        <FlaskConical size={22} />
      </div>
      <p className="panel-intro">
        每次仍重新检索和检查权限。只有会话、问题、模型与完整生成上下文一致，才能复用已有回答，减少生成调用。
      </p>
      <section className="experiment-box">
        <h3>固定上下文对照</h3>
        <p>{query || "先在左侧完成一次提问，再重复运行同一个问题。"}</p>
        <div className="experiment-actions">
          <button
            className="command-button"
            disabled={!query || !!busy}
            onClick={() => onRun(false)}
          >
            运行无缓存基线
          </button>
          <button
            className="command-button primary"
            disabled={!query || !!busy}
            onClick={() => onRun(true)}
          >
            运行加速查询
          </button>
        </div>
        <small>
          实验仅调用回答接口，不追加对话。首次加速运行可能未命中；再次运行才有机会复用。检索结果变化会使缓存失效。
        </small>
      </section>
      {!stats ? (
        <p className="empty-block">
          {trace
            ? "当前后端未返回加速指标，请更新后端后重试。"
            : "尚无测量结果。所有耗时和命中状态均来自后端。"}
        </p>
      ) : (
        <>
          <div className={`cache-state ${stats.cache_hit ? "hit" : ""}`}>
            <Activity size={18} />
            <strong>{status[stats.cache_status]}</strong>
            <span>少调用 {stats.avoided_model_calls} 次生成模型</span>
          </div>
          <section className="timings" aria-label="实际阶段耗时">
            {[
              ["检索与规划", stats.retrieval_ms],
              ["回答生成 / 复用", stats.generation_ms],
              ["端到端", stats.total_ms],
            ].map(([name, value]) => (
              <div className="timing-row" key={name}>
                <span>{name}</span>
                <strong>{duration(Number(value))}</strong>
                <meter
                  min={0}
                  max={Math.max(stats.total_ms, 1)}
                  value={Number(value)}
                  aria-label={String(name)}
                />
              </div>
            ))}
          </section>
          <table className="comparison-table">
            <caption>本次请求与最近基线（非统计基准）</caption>
            <thead>
              <tr>
                <th>指标</th>
                <th>无缓存基线</th>
                <th>本次</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <th>端到端耗时</th>
                <td>{baseline ? duration(baseline.elapsed_ms) : "尚未运行"}</td>
                <td>{duration(trace!.elapsed_ms)}</td>
              </tr>
              <tr>
                <th>候选 / 入选</th>
                <td>
                  {baseline
                    ? `${baseline.candidate_count} / ${baseline.selected_k}`
                    : "—"}
                </td>
                <td>
                  {trace!.candidate_count} / {trace!.selected_k}
                </td>
              </tr>
              <tr>
                <th>上下文估算 tokens</th>
                <td>{baseline?.context_tokens ?? "—"}</td>
                <td>
                  {stats.context_tokens_before} → {stats.context_tokens_after}
                </td>
              </tr>
            </tbody>
          </table>
          <p className="measurement-note">
            Token 数为字符数估算，不是供应商计费
            token。单次耗时受网络、模型和系统负载影响；没有重复采样时不宣称加速倍数。
          </p>
          {stats.original_generation_ms !== null && (
            <p className="measurement-note">
              缓存原始生成耗时：{duration(stats.original_generation_ms)}
              ，仅供追溯。
            </p>
          )}
          <details className="technical-detail">
            <summary>本次回答与策略</summary>
            <p>策略：{stats.strategy}</p>
            <div className="message-markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {trace!.generated_text}
              </ReactMarkdown>
            </div>
          </details>
        </>
      )}
    </div>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [snapshot, setSnapshot] = useState<SessionSnapshot | null>(null);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [hierarchy, setHierarchy] = useState<Hierarchy>(EMPTY_HIERARCHY);
  const [trace, setTrace] = useState<AnswerResult | null>(null);
  const [baseline, setBaseline] = useState<AnswerResult | null>(null);
  const [lastQuery, setLastQuery] = useState("");
  const [retryQuery, setRetryQuery] = useState("");
  const [tab, setTab] = useState<InspectorTab>("short");
  const [input, setInput] = useState("");
  const [topK, setTopK] = useState(5);
  const [accelerate, setAccelerate] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [showConnection, setShowConnection] = useState(false);
  const [evolution, setEvolution] = useState<Memory | null>(null);
  const [history, setHistory] = useState<HistoryEntry[] | null>(null);
  const [topic, setTopic] = useState("长短期记忆实验");
  const [project, setProject] = useState("memory-system-demo");
  const feedRef = useRef<HTMLDivElement>(null);
  const operationRef = useRef(false);
  const pendingUser = useRef<{ query: string; requestId: string } | null>(null);
  const pendingReply = useRef<{
    query: string;
    requestId: string;
    result?: AnswerResult;
  } | null>(null);
  const sessionDialogRef = useRef<HTMLDialogElement>(null);
  const messages = useMemo(
    () =>
      snapshot?.recent_turns.flatMap((turn) =>
        turn.events.map((event, index) => ({
          ...event,
          id: `${turn.turn_id}:${index}`,
        })),
      ) || [],
    [snapshot],
  );
  const shortCount =
    snapshot?.short_memories.filter((memory) => memory.status === "active")
      .length ?? 0;

  async function refresh(sessionId = snapshot?.session.session_id) {
    if (!sessionId) return;
    const [nextSnapshot, nextMemories, nextHierarchy] = await Promise.all([
      api.session(sessionId),
      api.memories(),
      api.hierarchy(),
    ]);
    setSnapshot(nextSnapshot);
    setMemories(nextMemories);
    setHierarchy(nextHierarchy);
  }
  async function createSession() {
    const session = await api.createSession(
      topic.trim() || "长短期记忆实验",
      project.trim(),
    );
    localStorage.setItem("memory-session-id", session.session_id);
    setTrace(null);
    setBaseline(null);
    setLastQuery("");
    setRetryQuery("");
    setInput("");
    setShowNew(false);
    pendingUser.current = null;
    pendingReply.current = null;
    await refresh(session.session_id);
  }
  async function operation(name: string, action: () => Promise<void>) {
    if (operationRef.current) return;
    operationRef.current = true;
    setBusy(name);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      operationRef.current = false;
      setBusy("");
    }
  }
  async function connect() {
    const modelHealth = await api.health();
    setHealth(modelHealth);
    const saved = localStorage.getItem("memory-session-id");
    if (saved) {
      try {
        await refresh(saved);
      } catch (cause) {
        if (!(cause instanceof ApiError) || cause.status !== 404) throw cause;
        await createSession();
      }
    } else await createSession();
  }
  useEffect(() => {
    void operation("连接记忆服务", connect);
  }, []);
  useEffect(() => {
    if (showNew) {
      sessionDialogRef.current?.showModal();
      sessionDialogRef.current
        ?.querySelector<HTMLInputElement>("input")
        ?.focus();
    } else sessionDialogRef.current?.close();
  }, [showNew]);
  useEffect(() => {
    feedRef.current?.scrollTo({
      top: feedRef.current.scrollHeight,
      behavior: "auto",
    });
  }, [messages.length, busy]);

  async function answer(query: string) {
    if (!snapshot) return;
    if (pendingReply.current?.query !== query)
      pendingReply.current = { query, requestId: crypto.randomUUID() };
    const pending = pendingReply.current;
    const result =
      pending.result ||
      (await api.answer(snapshot.session.session_id, query, topK, accelerate));
    pending.result = result;
    setTrace(result);
    setLastQuery(query);
    setBaseline(null);
    setTab("trace");
    await api.append(
      snapshot.session.session_id,
      "assistant",
      result.generated_text,
      pending.requestId,
    );
    setRetryQuery("");
    pendingReply.current = null;
    await refresh(snapshot.session.session_id);
  }
  function handleSend(event: FormEvent) {
    event.preventDefault();
    const query = input.trim();
    if (!query || !snapshot) return;
    void operation("检索记忆并生成回答", async () => {
      if (pendingUser.current?.query !== query)
        pendingUser.current = { query, requestId: crypto.randomUUID() };
      await api.append(
        snapshot.session.session_id,
        "user",
        query,
        pendingUser.current.requestId,
      );
      pendingUser.current = null;
      setInput("");
      setRetryQuery(query);
      await refresh(snapshot.session.session_id);
      await answer(query);
    });
  }
  function handleExtract() {
    if (!snapshot) return;
    void operation("从对话抽取记忆", async () => {
      const result = await api.extract(snapshot.session.session_id);
      await refresh(snapshot.session.session_id);
      setBaseline(null);
      setNotice(
        result.candidate_count
          ? `整理完成，识别 ${result.candidate_count} 条候选。可展开证据检查原文。`
          : "整理完成，本次没有可确认的新事实。可输入明确事实、偏好或纠正后再次整理。",
      );
    });
  }
  function handlePromote(memory: Memory) {
    setError("");
    setEvolution(memory);
  }
  function commitEvolution(command: EvolutionInput) {
    if (!evolution) return;
    void operation("应用记忆演化", async () => {
      await api.evolve(evolution, command);
      await refresh();
      setEvolution(null);
      setBaseline(null);
      if (["promote", "merge", "supersede"].includes(command.action))
        setTab("long");
      setNotice("演化已完成。检索使用更新后的有效状态，历史保留在审计记录中。");
    });
  }
  function showHistory(id: string) {
    void operation("读取演化历史", async () =>
      setHistory(await api.history(id)),
    );
  }
  function runExperiment(useAcceleration: boolean) {
    if (!snapshot || !lastQuery) return;
    void operation(
      useAcceleration ? "测量加速查询" : "测量无缓存基线",
      async () => {
        const result = await api.answer(
          snapshot.session.session_id,
          lastQuery,
          topK,
          useAcceleration,
        );
        setTrace(result);
        if (!useAcceleration) setBaseline(result);
        setTab("observe");
      },
    );
  }
  const steps: Array<{
    tab: InspectorTab;
    title: string;
    detail: string;
    icon: typeof BrainCircuit;
  }> = [
    {
      tab: "short",
      title: "短期整理",
      detail: `${snapshot?.pending_turns ?? 0} 条待处理 · ${shortCount} 条有效记忆`,
      icon: BrainCircuit,
    },
    {
      tab: "long",
      title: "长期演化",
      detail: `${hierarchy.domain_count} 个领域 · ${hierarchy.episode_count} 个版本`,
      icon: Database,
    },
    {
      tab: "trace",
      title: "按需检索",
      detail: trace?.plan
        ? `${label(trace.plan.route)} · ${trace.selected_k} 条入选`
        : "路由 → 筛选 → 证据",
      icon: Route,
    },
    {
      tab: "observe",
      title: "推理加速",
      detail: trace?.acceleration
        ? `${trace.acceleration.cache_hit ? "已命中" : "未复用"} · ${duration(trace.acceleration.total_ms)}`
        : "精确上下文缓存",
      icon: Activity,
    },
  ];
  return (
    <div className="app-shell">
      <a className="skip-link" href="#message-input">
        跳到消息输入
      </a>
      <header className="app-header">
        <div className="brand">
          <BrainCircuit size={27} aria-hidden="true" />
          <div>
            <strong>SimpleMem</strong>
            <span>长短期记忆工作台</span>
          </div>
        </div>
        <div className="model-status">
          <span
            className={health?.status === "ok" ? "status-dot ok" : "status-dot"}
          />
          <span>
            {health
              ? health.model_configured
                ? health.model_name
                : "后端在线 · 模型未配置"
              : "尚未连接"}
          </span>
          <em>{health?.model_provider || "Memory Engine"}</em>
        </div>
        <div className="header-actions">
          <button
            className="icon-button"
            aria-label="连接设置"
            title="连接设置"
            disabled={!!busy}
            onClick={() => {
              setError("");
              setShowConnection(true);
            }}
          >
            <Settings2 size={17} />
          </button>
          <button
            className="icon-button"
            type="button"
            title="重新连接并刷新"
            aria-label="重新连接并刷新"
            onClick={() => void operation("刷新数据", connect)}
            disabled={!!busy}
          >
            <RefreshCw size={17} />
          </button>
          <button
            className="command-button"
            type="button"
            onClick={() => setShowNew(true)}
            disabled={!!busy}
          >
            <Plus size={16} />
            新会话
          </button>
        </div>
      </header>
      <section className="mechanism" aria-label="记忆运行机制">
        <div className="mechanism-intro">
          <span className="live-label">记忆生命周期</span>
          <p>
            让对话成为
            <br />
            <strong>可追溯的知识。</strong>
          </p>
        </div>
        <div className="mechanism-steps">
          {steps.map((step, index) => (
            <button
              className={`mechanism-step ${tab === step.tab ? "selected" : ""}`}
              key={step.tab}
              onClick={() => setTab(step.tab)}
              aria-pressed={tab === step.tab}
            >
              <span className="step-icon">
                <step.icon size={21} />
              </span>
              <span>
                <strong>{step.title}</strong>
                <small>{step.detail}</small>
              </span>
              {index < steps.length - 1 && (
                <ArrowRight className="flow-arrow" size={15} />
              )}
            </button>
          ))}
        </div>
      </section>
      <div className="global-feedback" aria-live="polite">
        {busy && (
          <p className="busy-bar" role="status">
            <LoaderCircle size={15} className="spin" />
            {busy}…
          </p>
        )}
        {error && (
          <div className="error-bar" role="alert">
            <span>{error}</span>
            {retryQuery && (
              <button
                onClick={() =>
                  void operation("重试回答", () => answer(retryQuery))
                }
                disabled={!!busy}
              >
                重试已保存的问题
              </button>
            )}
            <button aria-label="关闭错误提示" onClick={() => setError("")}>
              <X size={16} />
            </button>
          </div>
        )}
        {notice && (
          <p className="notice-bar">
            <ShieldCheck size={16} />
            {notice}
          </p>
        )}
      </div>
      <main className="workspace">
        <section className="conversation" aria-label="真实对话">
          <div className="conversation-header">
            <div>
              <h1>{snapshot?.session.topic || "连接你的记忆服务"}</h1>
              <span title={snapshot?.session.session_id}>
                {snapshot
                  ? `项目：${snapshot.session.project_id || "无"} / 会话 ${snapshot.session.session_id.slice(0, 12)}`
                  : apiBase}
              </span>
            </div>
            <label className="top-k-control">
              检索上限
              <input
                aria-label="检索上限 Top K"
                type="number"
                min="1"
                max="20"
                value={topK}
                disabled={!!busy}
                onChange={(event) => {
                  setTopK(
                    Math.max(1, Math.min(20, Number(event.target.value) || 1)),
                  );
                  setBaseline(null);
                }}
              />
            </label>
          </div>
          <div className="message-feed" ref={feedRef}>
            {messages.length === 0 && (
              <div className="chat-empty">
                <MessageSquareText size={35} />
                <h2>从一条值得记住的信息开始</h2>
                <p>
                  写入事实或偏好，整理成短期记忆，再观察它如何进入长期知识和后续回答。
                </p>
                <button
                  className="example-prompt"
                  disabled={!snapshot || !!busy}
                  onClick={() =>
                    setInput(
                      "我正在开发一个长短期记忆系统，后端使用 Python。我偏好中文说明，示例代码请附测试。",
                    )
                  }
                >
                  试试：告诉系统你的项目和偏好
                  <ArrowRight size={16} />
                </button>
                <small>示例只填入输入框，发送后才会写入真实数据。</small>
              </div>
            )}
            {messages.map((message) => (
              <article
                className={`message message-${message.role}`}
                key={message.id}
              >
                <div className="message-meta">
                  <strong>{roleName(message.role)}</strong>
                  <time dateTime={message.occurred_at}>
                    {formatTime(message.occurred_at)}
                  </time>
                </div>
                {message.role === "assistant" ? (
                  <div className="message-markdown">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {message.content}
                    </ReactMarkdown>
                  </div>
                ) : (
                  <p>{message.content}</p>
                )}
              </article>
            ))}
          </div>
          <form className="composer" onSubmit={handleSend}>
            <div className="composer-field">
              <label className="sr-only" htmlFor="message-input">
                输入消息
              </label>
              <textarea
                id="message-input"
                value={input}
                maxLength={3000}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (
                    event.key === "Enter" &&
                    !event.shiftKey &&
                    !event.nativeEvent.isComposing
                  ) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
                placeholder="描述事实、提出问题，或纠正旧信息…"
                rows={3}
                disabled={!snapshot || !!busy}
              />
              <div className="composer-options">
                <label>
                  <input
                    type="checkbox"
                    checked={accelerate}
                    onChange={(event) => setAccelerate(event.target.checked)}
                    disabled={!!busy}
                  />
                  启用生成缓存
                </label>
                <span>{input.length}/3000 · Shift + Enter 换行</span>
              </div>
            </div>
            <button
              className="send-button"
              type="submit"
              title="发送消息"
              aria-label="发送消息"
              disabled={!input.trim() || !snapshot || !!busy}
            >
              <Send size={18} />
            </button>
          </form>
        </section>
        <aside className="inspector" aria-label="记忆机制检查器">
          <nav className="tabs" aria-label="检查器视图">
            {steps.map((step) => (
              <button
                key={step.tab}
                type="button"
                className={tab === step.tab ? "active" : ""}
                aria-pressed={tab === step.tab}
                onClick={() => setTab(step.tab)}
              >
                <step.icon size={15} />
                {
                  {
                    short: "短期",
                    long: "长期",
                    trace: "检索",
                    observe: "观测",
                  }[step.tab]
                }
              </button>
            ))}
          </nav>
          {tab === "short" && (
            <ShortMemoryPanel
              snapshot={snapshot}
              memories={memories}
              onExtract={handleExtract}
              onPromote={handlePromote}
              busy={busy}
            />
          )}
          {tab === "long" && (
            <HierarchyPanel
              hierarchy={hierarchy}
              memories={memories}
              busy={!!busy}
              onHistory={showHistory}
              onEvolve={handlePromote}
            />
          )}
          {tab === "trace" && <TracePanel trace={trace} />}
          {tab === "observe" && (
            <ObservePanel
              trace={trace}
              baseline={baseline}
              query={lastQuery}
              busy={busy}
              onRun={runExperiment}
            />
          )}
        </aside>
      </main>
      {evolution && (
        <EvolutionDialog
          memory={evolution}
          memories={memories}
          busy={!!busy}
          error={error}
          onClose={() => {
            if (!busy) setEvolution(null);
          }}
          onCommit={commitEvolution}
        />
      )}
      {history && (
        <HistoryDialog entries={history} onClose={() => setHistory(null)} />
      )}
      {showConnection && (
        <ConnectionDialog
          initial={getServiceToken()}
          error={error}
          busy={!!busy}
          onClose={() => setShowConnection(false)}
          onSave={(token) => {
            setServiceToken(token);
            void operation("连接记忆服务", async () => {
              await connect();
              setShowConnection(false);
            });
          }}
        />
      )}
      <footer className="workspace-footer">
        <span>
          <ShieldCheck size={13} />
          {health?.scope_mode || "等待作用域信息"}
        </span>
        <span>
          {health?.semantic_retrieval ? "语义检索已接入" : "语义检索未启用"}
        </span>
        <span>数据来自后端 · 模型密钥仅在服务端配置</span>
      </footer>
      <dialog
        ref={sessionDialogRef}
        className="session-dialog"
        aria-labelledby="session-dialog-title"
        onCancel={() => setShowNew(false)}
      >
        <div className="section-heading">
          <h2 id="session-dialog-title">开始新的会话</h2>
          <button
            className="icon-button"
            aria-label="关闭新会话窗口"
            onClick={() => setShowNew(false)}
          >
            <X size={16} />
          </button>
        </div>
        <p>
          短期记忆随会话隔离。相同项目下的长期记忆可供新会话检索，用户级记忆跨项目生效。
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void operation("创建会话", createSession);
          }}
        >
          <label>
            会话主题
            <input
              autoFocus
              required
              maxLength={200}
              value={topic}
              onChange={(event) => setTopic(event.target.value)}
            />
          </label>
          <label>
            项目标识
            <input
              required
              maxLength={100}
              value={project}
              onChange={(event) => setProject(event.target.value)}
            />
          </label>
          <button
            className="command-button primary"
            type="submit"
            disabled={!!busy}
          >
            创建并切换
          </button>
        </form>
      </dialog>
    </div>
  );
}
