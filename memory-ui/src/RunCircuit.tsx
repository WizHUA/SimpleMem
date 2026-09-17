import { useEffect, useState } from "react";
import {
  ArrowRight,
  BrainCircuit,
  Check,
  Database,
  Play,
  RotateCcw,
  Route,
  ScanSearch,
  Square,
  Zap,
} from "lucide-react";
import type { AnswerResult } from "./types";

export interface RunEvent {
  type: "stage";
  phase: string;
  detail: string;
  elapsed_ms: number;
}
export type RunMode = "idle" | "live" | "recorded" | "error";
const STAGES = [
  {
    phase: "extraction",
    title: "理解与写入",
    description: "核验证据 · 区分提问和事实",
    icon: BrainCircuit,
  },
  {
    phase: "planning",
    title: "规划查询",
    description: "范围 · 字段 · 时间",
    icon: Route,
  },
  {
    phase: "retrieval",
    title: "寻找记忆",
    description: "短期优先 · 长期补充",
    icon: Database,
  },
  {
    phase: "generation",
    title: "组织回答",
    description: "证据上下文 · 精确复用",
    icon: Zap,
  },
  {
    phase: "completed",
    title: "核对来源",
    description: "查看引用与实际运行记录",
    icon: ScanSearch,
  },
];

export function RunCircuit({
  events,
  mode,
  result,
  onInspect,
}: {
  events: RunEvent[];
  mode: RunMode;
  result: AnswerResult | null;
  onInspect: (tab: "short" | "long" | "trace" | "observe") => void;
}) {
  const [cursor, setCursor] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    setCursor(null);
    setPlaying(false);
  }, [mode, events.length]);
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(
      () =>
        setCursor((value) => {
          const next = (value ?? -1) + 1;
          if (next >= events.length - 1) setPlaying(false);
          return Math.min(next, events.length - 1);
        }),
      1100,
    );
    return () => window.clearInterval(timer);
  }, [playing, events.length]);
  const replaying = cursor !== null;
  const shown = replaying ? events.slice(0, cursor + 1) : events;
  const latest = shown.at(-1);
  const completed = mode === "recorded" && !replaying;
  const title = replaying
    ? "记录回放 · 节奏已压缩"
    : mode === "live"
      ? "正在运行 · 服务事件"
      : mode === "error"
        ? "运行中断 · 保留已发生阶段"
        : mode === "recorded"
          ? "运行完成 · 实际记录"
          : "一次回答如何经过记忆系统";
  return (
    <section className={`run-circuit ${mode}`} aria-label="记忆运行机制">
      <div className="run-heading">
        <div>
          <span
            className={`run-indicator ${mode === "live" ? "active" : ""}`}
          />
          <strong>{title}</strong>
          <span className="run-disclaimer">
            可观测服务阶段，不包含模型内部思考
          </span>
        </div>
        <div className="run-controls">
          {latest && <time>{(latest.elapsed_ms / 1000).toFixed(2)} s</time>}
          {mode !== "live" && events.length > 0 && (
            <button
              type="button"
              onClick={() => {
                if (playing) {
                  setPlaying(false);
                } else {
                  setCursor(-1);
                  setPlaying(true);
                }
              }}
              aria-label={playing ? "暂停记录回放" : "回放运行记录"}
            >
              {playing ? <Square size={13} /> : <Play size={13} />}
              {playing ? "暂停" : "回放"}
            </button>
          )}
          {replaying && (
            <button
              type="button"
              aria-label="退出记录回放"
              onClick={() => {
                setPlaying(false);
                setCursor(null);
              }}
            >
              <RotateCcw size={13} />
            </button>
          )}
        </div>
      </div>
      <div className="circuit-track">
        {STAGES.map((stage, index) => {
          const seen = shown.some((event) => event.phase === stage.phase);
          const active = latest?.phase === stage.phase && !completed;
          return (
            <div
              className={`circuit-station ${seen ? "visited" : ""} ${active ? "running" : ""}`}
              key={stage.phase}
            >
              <button
                type="button"
                onClick={() =>
                  onInspect(
                    stage.phase === "extraction"
                      ? "short"
                      : stage.phase === "generation"
                        ? "observe"
                        : "trace",
                  )
                }
                aria-label={`查看${stage.title}`}
              >
                <span className="circuit-symbol">
                  <stage.icon size={21} />
                  {seen && !active && (
                    <Check className="station-check" size={11} />
                  )}
                </span>
                <span>
                  <strong>{stage.title}</strong>
                  <small>
                    {stage.phase === "generation" &&
                    result?.acceleration &&
                    completed
                      ? `缓存 ${result.acceleration.cache_status} · 少 ${result.acceleration.avoided_model_calls} 次生成`
                      : stage.description}
                  </small>
                </span>
              </button>
              {stage.phase === "retrieval" && (
                <div className="memory-branches">
                  <button type="button" onClick={() => onInspect("short")}>
                    短期工作区
                  </button>
                  <span>↔</span>
                  <button type="button" onClick={() => onInspect("long")}>
                    长期知识库
                  </button>
                </div>
              )}
              {index < STAGES.length - 1 && (
                <ArrowRight className="circuit-wire" size={18} />
              )}
            </div>
          );
        })}
      </div>
      <div className="run-caption" aria-live="polite">
        <span>
          {latest?.detail ||
            "写入事实、提出问题，然后观察抽取、查询与回答的真实过程。"}
        </span>
        {result && completed && (
          <span>
            {result.candidate_count} 条候选 → {result.selected_k} 条入选证据
          </span>
        )}
      </div>
    </section>
  );
}

export function RunTimeline({
  events,
  mode,
}: {
  events: RunEvent[];
  mode: RunMode;
}) {
  if (!events.length) return null;
  return (
    <section className="run-timeline" aria-label="服务阶段时间线">
      <h3>{mode === "live" ? "阶段正在推进" : "本次运行记录"}</h3>
      <p>时间为服务端累计耗时；阶段记录不等于模型思维链。</p>
      <ol>
        {events.map((event, index) => (
          <li
            key={`${index}:${event.phase}`}
            className={
              mode === "live" && index === events.length - 1 ? "current" : ""
            }
          >
            <time>{(event.elapsed_ms / 1000).toFixed(2)}s</time>
            <div>
              <strong>
                {STAGES.find((stage) => stage.phase === event.phase)?.title ||
                  event.phase}
              </strong>
              <p>{event.detail}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

const SCENARIOS = [
  {
    title: "别名与反问",
    scope: "同一会话 · 短期",
    expectation:
      "先陈述再追问。疑问句本身不应成为确认事实；不要把代号解释成动物类别。",
    messages: [
      "zfc 的昵称是飞猪。这里的飞猪只是我朋友的昵称。",
      "zfc 是飞猪吗？",
      "zfc 不是飞猪吗？我是在确认之前的昵称。",
      "从现在起，zfc 的昵称从飞猪改为小鹿，飞猪是旧昵称。",
      "zfc 现在和以前分别叫什么？",
    ],
  },
  {
    title: "跨会话与临时覆盖",
    scope: "同项目新会话 · 长期",
    expectation:
      "先保存项目规则，再用顶部“新会话”进入相同项目。临时例外只影响当前会话，另开会话应恢复长期规则。",
    messages: [
      "请长期记住：当前项目默认使用 PostgreSQL 数据库，文档使用中文。",
      "当前项目默认数据库和文档语言是什么？",
      "仅本次会话的演示临时使用 SQLite，不修改项目默认数据库。",
      "本次演示用什么数据库？项目默认又是什么？",
    ],
  },
  {
    title: "长对话与纠正",
    scope: "多轮干扰 · 时间变化",
    expectation:
      "按顺序发送，并在中间补充多轮其他话题。检索应保留负责人并选择新日期，查历史时再返回旧日期。",
    messages: [
      "请长期记住：当前项目的发布负责人是林悦，计划发布日期是 2026 年 12 月 5 日。",
      "本次会议先讨论界面配色，采用蓝灰色；测试环境先用本地机器。",
      "补充测试安排：先做单元测试，再做并发和恢复测试。",
      "更新项目计划：发布日期从 2026 年 12 月 5 日改为 2026 年 12 月 12 日，负责人不变。",
      "谁负责发布，当前日期是什么？之前计划的日期是什么？",
    ],
  },
];
export function ScenarioGuide({
  onFill,
  disabled,
}: {
  onFill: (value: string) => void;
  disabled: boolean;
}) {
  const [selected, setSelected] = useState(0);
  const scenario = SCENARIOS[selected];
  return (
    <details className="scenario-guide">
      <summary>
        用真实场景检验记忆 <span>别名 / 跨会话 / 长对话</span>
      </summary>
      <div className="scenario-body">
        <div className="scenario-tabs">
          {SCENARIOS.map((item, index) => (
            <button
              type="button"
              key={item.title}
              aria-pressed={selected === index}
              onClick={() => setSelected(index)}
            >
              {item.title}
            </button>
          ))}
        </div>
        <p>
          <strong>{scenario.scope}</strong> {scenario.expectation}
        </p>
        <ol>
          {scenario.messages.map((message, index) => (
            <li key={message}>
              <span>{index + 1}</span>
              <button
                disabled={disabled}
                type="button"
                onClick={() => onFill(message)}
                title="填入输入框"
              >
                {message}
              </button>
            </li>
          ))}
        </ol>
        <small>
          点击只填入输入框，发送才写入当前真实会话。步骤需要手动推进，测试结论应以证据和历史记录为准。
        </small>
      </div>
    </details>
  );
}
