import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { BookOpen, X } from "lucide-react";
import type { AnswerContext, Hit } from "./types";

type MarkdownNode = { type: string; value?: string; children?: MarkdownNode[]; url?: string; data?: Record<string, unknown> };
// Operate on parsed text only: fenced/inline code, authored links and images stay intact.
function citationPlugin() {
  return (tree: MarkdownNode) => {
    const visit = (node: MarkdownNode) => {
      if (["link", "linkReference", "code", "inlineCode", "image", "imageReference"].includes(node.type) || !node.children) return;
      node.children = node.children.flatMap((child): MarkdownNode[] => {
        if (child.type !== "text") { visit(child); return [child]; }
        const value = child.value || "";
        const pieces: MarkdownNode[] = [];
        let offset = 0;
        for (const match of value.matchAll(/【来源\s*(\d+)】/g)) {
          if (match.index! > offset) pieces.push({ type: "text", value: value.slice(offset, match.index) });
          pieces.push({ type: "link", url: "#citation", children: [{ type: "text", value: match[1] }], data: { hProperties: { "data-memory-citation": match[1] } } });
          offset = match.index! + match[0].length;
        }
        if (offset < value.length) pieces.push({ type: "text", value: value.slice(offset) });
        return pieces.length ? pieces : [child];
      });
    };
    visit(tree);
  };
}
function text(value: unknown) { return typeof value === "string" || typeof value === "number" ? String(value) : "未记录"; }
function date(value: unknown) {
  if (typeof value !== "string" || !value) return "未限定";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false });
}
export function SourceDialog({ source, number, available, onClose }: { source?: Hit; number: number; available: boolean; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const trigger = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => { dialog.current?.close(); trigger?.focus(); };
  }, []);
  const meta = source?.metadata || {};
  const evidence = Array.isArray(meta.evidence) ? meta.evidence as Array<Record<string, unknown>> : [];
  return <dialog ref={dialog} className="source-dialog" aria-labelledby="source-dialog-title" onCancel={onClose} onClick={(event) => { if (event.target === event.currentTarget) { const box = event.currentTarget.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) onClose(); } }}>
    <header><div className="source-title"><BookOpen size={18} /><div><h2 id="source-dialog-title">来源 {number}</h2><p>这条回答使用的证据</p></div></div><button className="icon-button" aria-label="关闭来源" onClick={onClose}><X size={17} /></button></header>
    {source ? <div className="source-dialog-body">
      <div className="source-tags"><span>{meta.tier === "long" ? "长期记忆" : meta.tier === "short" ? "短期记忆" : "记忆来源"}</span><span>版本 {text(meta.version)}</span><span>{{ session: "当前会话", project: "项目共享", user: "用户共享" }[String(meta.scope_type)] || text(meta.scope_type)}</span></div>
      <h3>记忆内容</h3><p className="source-content">{source.content}</p>
      <h3>原始证据</h3>{evidence.length ? evidence.map((item, index) => <figure key={index}><blockquote>{text(item.quote)}</blockquote><figcaption>事件 {typeof item.event_index === "number" ? item.event_index + 1 : "未记录"} · {text(item.turn_id)}</figcaption></figure>) : <p className="source-empty">这条来源没有附带原文证据。</p>}
      <dl className="source-facts"><div><dt>记录时间</dt><dd>{date(meta.recorded_at)}</dd></div><div><dt>有效时间</dt><dd>{date(meta.valid_from)} — {date(meta.valid_to)}</dd></div><div><dt>状态快照</dt><dd>{{ active: "有效", superseded: "已替代", pending: "待确认", archived: "已归档", retracted: "已撤回" }[String(meta.status)] || text(meta.status)}</dd></div><div><dt>作用范围</dt><dd>{text(meta.scope_id)}</dd></div></dl>
      <details className="source-technical"><summary>来源标识与匹配信息</summary><p>{source.source_file}</p><p>{source.chunk_id}</p><p>检索相关性 {source.score.toFixed(3)}，不代表事实可信度。</p>{Array.isArray(meta.matched_views) && <p>{meta.matched_views.join(" / ")}</p>}</details>
      <p className="source-snapshot-note">保留的是生成这条回答时的证据快照，后续记忆更新不会改写此处。</p>
    </div> : <div className="source-dialog-body"><h3>{available ? "无法定位此来源" : "来源记录不可用"}</h3><p className="source-empty">{available ? "此编号不在这条回答的来源列表中。" : "这是一条未保存来源快照的历史回答。重新提问可以生成带可查看来源的新回答。"}</p></div>}
  </dialog>;
}
export function AnswerMessage({ content, context }: { content: string; context?: AnswerContext | null }) {
  const [selected, setSelected] = useState<number | null>(null);
  const components = useMemo(() => ({ a: ({ node, children, ...props }: import("react-markdown").ExtraProps & import("react").ComponentPropsWithoutRef<"a">) => {
    const number = Number(node?.properties?.["data-memory-citation"]);
    if (Number.isInteger(number) && number >= 0) return <button type="button" className={`citation-chip ${context?.sources[number - 1] ? "" : "unavailable"}`} aria-label={`查看来源 ${number}`} onClick={() => setSelected(number)}>{number}</button>;
    return <a {...props} rel="noopener noreferrer">{children}</a>;
  } }), [context]);
  return <>
    <div className="message-markdown"><ReactMarkdown remarkPlugins={[remarkGfm, citationPlugin]} components={components}>{content}</ReactMarkdown></div>
    {selected !== null && <SourceDialog number={selected} source={context?.sources[selected - 1]} available={!!context} onClose={() => setSelected(null)} />}
  </>;
}
