import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import type { EvolutionInput, HistoryEntry, Memory } from "./types";

const ACTIONS: Record<string, string> = {
  promote: "晋升长期",
  merge: "合并相同事实证据",
  supersede: "替代旧版本",
  retract: "撤回记忆",
  defer: "暂缓确认",
  add: "新增记忆",
  close_validity: "关闭旧版本有效期",
  consume_candidate: "消费短期候选",
};

function localNow() {
  const date = new Date();
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}

function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
    ref.current?.querySelector<HTMLElement>("input,select,button")?.focus();
  }, []);
  return (
    <dialog
      ref={ref}
      className="session-dialog evolution-dialog"
      aria-label={title}
      onCancel={onClose}
    >
      <div className="section-heading">
        <h2>{title}</h2>
        <button className="icon-button" aria-label="关闭窗口" onClick={onClose}>
          <X size={16} />
        </button>
      </div>
      {children}
    </dialog>
  );
}

export function EvolutionDialog({
  memory,
  memories,
  busy,
  error,
  onClose,
  onCommit,
}: {
  memory: Memory;
  memories: Memory[];
  busy: boolean;
  error: string;
  onClose: () => void;
  onCommit: (input: EvolutionInput) => void;
}) {
  const targets = memories.filter(
    (item) =>
      item.tier === "long" &&
      item.status === "active" &&
      item.memory_id !== memory.memory_id &&
      item.scope_type === memory.scope_type &&
      item.scope_id === memory.scope_id &&
      item.subject === memory.subject &&
      item.predicate === memory.predicate,
  );
  const eligible =
    memory.tier === "short" &&
    memory.durable &&
    memory.scope_type !== "session" &&
    !["hypothetical", "inferred"].includes(memory.assertion) &&
    memory.status === "active";
  const [action, setAction] = useState<EvolutionInput["action"]>(
    eligible ? (targets.length ? "supersede" : "promote") : "retract",
  );
  const [targetId, setTargetId] = useState(targets[0]?.memory_id || "");
  const [effective, setEffective] = useState(localNow());
  const target = targets.find((item) => item.memory_id === targetId);
  const needsTarget = action === "supersede" || action === "merge";
  const sameFact =
    target &&
    target.content === memory.content &&
    target.value === memory.value &&
    target.valid_from === memory.valid_from &&
    target.valid_to === memory.valid_to;
  return (
    <Modal title="审阅记忆演化" onClose={onClose}>
      <p className="panel-intro">
        操作携带当前版本号，服务端会再次检查作用域、事实关系和版本，防止覆盖并发修改。
      </p>
      <blockquote className="review-memory">
        {memory.content}
        <small>
          当前 v{memory.version} · {memory.scope_type} / {memory.scope_id}
        </small>
      </blockquote>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onCommit({
            action,
            ...(needsTarget && target
              ? { target_id: target.memory_id, target_version: target.version }
              : {}),
            ...(action === "supersede"
              ? { effective_at: new Date(effective).toISOString() }
              : {}),
          });
        }}
      >
        <label>
          演化动作
          <select
            aria-label="演化动作"
            value={action}
            onChange={(event) =>
              setAction(event.target.value as EvolutionInput["action"])
            }
            disabled={busy}
          >
            {eligible && (
              <>
                <option value="promote" disabled={targets.length > 0}>
                  晋升长期{targets.length ? "（存在相关长期事实）" : ""}
                </option>
                <option value="supersede" disabled={!targets.length}>
                  替代旧版本
                </option>
                <option value="merge" disabled={!targets.length}>
                  合并相同事实证据
                </option>
              </>
            )}
            {memory.status === "active" && (
              <option value="defer">暂缓确认</option>
            )}
            <option value="retract">撤回记忆</option>
          </select>
        </label>
        {needsTarget && (
          <label>
            目标长期版本
            <select
              aria-label="目标长期版本"
              value={targetId}
              onChange={(event) => setTargetId(event.target.value)}
              required
              disabled={busy}
            >
              {targets.map((item) => (
                <option key={item.memory_id} value={item.memory_id}>
                  v{item.version} · {item.content}
                </option>
              ))}
            </select>
          </label>
        )}
        {action === "supersede" && (
          <>
            <blockquote className="review-memory old">
              旧版本：{target?.content}
            </blockquote>
            <label>
              新版本生效时间（本地时区）
              <input
                type="datetime-local"
                value={effective}
                onChange={(event) => setEffective(event.target.value)}
                required
                disabled={busy}
              />
            </label>
            <p className="measurement-note">
              旧版本在此时间结束，新版本从此时间开始。旧证据与历史仍可审计。
            </p>
          </>
        )}
        {action === "merge" && (
          <p className="measurement-note">
            只合并内容、值和有效期完全一致的事实，保留双方证据。
            {!sameFact && "当前两条记忆不满足精确合并条件，请选择替代或暂缓。"}
          </p>
        )}
        {action === "retract" && (
          <p className="destructive-note">
            确认后该记忆退出正常检索，审计历史仍会保留。此动作不会删除原始对话。
          </p>
        )}
        {action === "defer" && (
          <p className="measurement-note">
            记忆转为待确认状态并退出正常检索。当前版本暂不提供恢复操作；后续可重新提供事实并抽取。
          </p>
        )}
        {error && (
          <p className="destructive-note" role="alert">
            {error}
          </p>
        )}
        <button
          className={`command-button ${action === "retract" ? "danger" : "primary"}`}
          type="submit"
          disabled={
            busy ||
            (needsTarget && !target) ||
            (action === "merge" && !sameFact)
          }
        >
          {busy ? "正在应用…" : `确认${ACTIONS[action]}`}
        </button>
      </form>
    </Modal>
  );
}

export function HistoryDialog({
  entries,
  onClose,
}: {
  entries: HistoryEntry[];
  onClose: () => void;
}) {
  function content(json: string | null) {
    if (!json) return "无";
    try {
      const value = JSON.parse(json);
      return `${value.content || ""}（v${value.version}，${value.status}）`;
    } catch {
      return "无法解析历史内容";
    }
  }
  return (
    <Modal title="记忆演化历史" onClose={onClose}>
      <p className="panel-intro">
        以下记录直接来自后端审计日志，包括被替代、暂缓与撤回的版本。
      </p>
      {entries.length ? (
        <ol className="history-list">
          {entries.map((entry, index) => (
            <li key={index}>
              <strong>{ACTIONS[entry.action] || entry.action}</strong>
              <time>{entry.recorded_at}</time>
              <dl>
                <dt>变更前</dt>
                <dd>{content(entry.before_json)}</dd>
                <dt>变更后</dt>
                <dd>{content(entry.after_json)}</dd>
              </dl>
            </li>
          ))}
        </ol>
      ) : (
        <p className="empty-block">暂无历史记录。</p>
      )}
    </Modal>
  );
}

export function ConnectionDialog({
  initial,
  error,
  busy,
  onClose,
  onSave,
}: {
  initial: string;
  error: string;
  busy: boolean;
  onClose: () => void;
  onSave: (token: string) => void;
}) {
  const [token, setToken] = useState(initial);
  return (
    <Modal title="连接记忆服务" onClose={onClose}>
      <p className="panel-intro">
        填写后端 MEMORY_API_KEY
        对应的访问令牌。这里只用于访问记忆服务，不接受模型供应商密钥。令牌保存在当前浏览器标签页的
        sessionStorage，关闭标签页后清除。
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onSave(token);
        }}
      >
        <label>
          服务访问令牌
          <input
            type="password"
            autoComplete="off"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="本地无鉴权模式可留空"
          />
        </label>
        {error && (
          <p className="destructive-note" role="alert">
            {error}
          </p>
        )}
        <button
          className="command-button primary"
          type="submit"
          disabled={busy}
        >
          保存并重新连接
        </button>
      </form>
    </Modal>
  );
}
