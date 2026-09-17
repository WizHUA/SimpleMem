import { useState } from "react";
export interface RunEvent {
  type: "stage";
  phase: string;
  detail: string;
  elapsed_ms: number;
  plan?: import("./types").QueryPlan;
  steps?: import("./types").QueryStep[];
  sources?: import("./types").Hit[];
}
export type RunMode = "idle" | "live" | "recorded" | "error";
const STAGES = [
  { phase: "extraction", title: "理解与写入" },
  { phase: "planning", title: "规划查询" },
  { phase: "retrieval", title: "寻找记忆" },
  { phase: "generation", title: "组织回答" },
  { phase: "completed", title: "核对来源" },
];

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
