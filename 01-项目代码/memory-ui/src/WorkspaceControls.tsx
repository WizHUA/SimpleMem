import { useEffect, useRef, useState } from "react";
import { Check, MessagesSquare, Search, X } from "lucide-react";
import { api } from "./api";
import type { Session } from "./types";

export function SessionPicker({ currentId, disabled, onSelect }: { currentId?: string; disabled: boolean; onSelect: (id: string) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) { dialog.current?.close(); return; }
    dialog.current?.showModal();
    let active = true;
    setLoading(true); setError(""); setSessions([]); setQuery("");
    api.sessions().then((items) => { if (active) setSessions(items); }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : "会话列表加载失败");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; trigger.current?.focus(); };
  }, [open]);
  const filtered = sessions.filter((session) => `${session.topic} ${session.project_id || ""} ${session.session_id}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  return <>
    <button ref={trigger} className="command-button session-switch" title="切换会话" disabled={disabled} onClick={() => setOpen(true)}><MessagesSquare size={16} />切换会话</button>
    <dialog ref={dialog} className="session-picker" aria-labelledby="session-picker-title" onCancel={() => setOpen(false)}>
      <header><div><h2 id="session-picker-title">你的会话</h2><p>继续已有对话，短期记忆跟随会话切换。</p></div><button className="icon-button" aria-label="关闭会话列表" onClick={() => setOpen(false)}><X size={18} /></button></header>
      <label className="session-search"><Search size={17} /><input autoFocus aria-label="搜索会话" placeholder="搜索主题、项目或会话编号" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <div className="session-results" aria-live="polite">
        {loading ? <p>正在读取会话…</p> : error ? <p role="alert">{error}</p> : !filtered.length ? <p>没有匹配的会话。</p> : filtered.map((session) => <button className="session-option" key={session.session_id} disabled={disabled} aria-current={session.session_id === currentId ? "true" : undefined} onClick={async () => { if (session.session_id !== currentId) await onSelect(session.session_id); setOpen(false); }}>
          <div><strong>{session.topic || "未命名会话"}</strong><span>{session.project_id || "无项目"} · {session.session_id.slice(-8)}</span><time>{new Date(session.updated_at || session.created_at).toLocaleString("zh-CN", { hour12: false })}</time></div>
          {session.session_id === currentId && <Check size={17} aria-label="当前会话" />}
        </button>)}
      </div>
      <footer>按最近活动排序 · 仅显示当前身份可访问的会话</footer>
    </dialog>
  </>;
}

export function ResizeHandle({ width, onResize }: { width: number; onResize: (width: number) => void }) {
  const clamp = (value: number) => Math.round(Math.max(320, Math.min(720, window.innerWidth - 420, value)));
  return <div className="inspector-resizer" role="separator" tabIndex={0} aria-label="调整记忆面板宽度" aria-orientation="vertical" aria-valuemin={320} aria-valuemax={720} aria-valuenow={width}
    title="拖动调整宽度；方向键微调；双击恢复"
    onPointerDown={(event) => { event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); }}
    onPointerMove={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) { const right = event.currentTarget.parentElement!.getBoundingClientRect().right; onResize(clamp(right - event.clientX)); } }}
    onPointerUp={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
    onDoubleClick={() => onResize(420)}
    onKeyDown={(event) => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) { event.preventDefault(); onResize(clamp(event.key === "Home" ? 320 : event.key === "End" ? 720 : width + (event.key === "ArrowLeft" ? 24 : -24))); } }}><span /></div>;
}
