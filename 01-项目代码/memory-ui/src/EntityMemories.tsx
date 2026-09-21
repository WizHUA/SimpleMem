import { Layers3 } from "lucide-react";
import type { EntityMemory, Memory } from "./types";
import "./EntityMemories.css";

export function EntityMemories({ entities, busy, onHistory, onEvolve }: { entities: EntityMemory[]; busy: boolean; onHistory: (id: string) => void; onEvolve: (memory: Memory) => void }) {
  return <div className="entity-memories">{entities.map((entity) => <article className="entity-memory" key={entity.entity_id}>
    <header><div><Layers3 size={18} /><h3>{entity.subject}</h3></div><span>{{ user: "用户记忆", project: "项目记忆", session: "会话记忆" }[entity.scope_type] || entity.scope_type}</span></header>
    <p className="entity-summary">{entity.summary}</p>
    {!!entity.conflict_predicates.length && <p className="entity-conflict">待核对：{entity.conflict_predicates.join("、")}存在不同取值，尚未确认为一致事实。</p>}
    <details className="entity-attributes"><summary>{entity.facts.length} 条属性 · 查看来源与演化</summary>
      {entity.facts.map((fact) => <div className="entity-fact" key={`${fact.memory_id}:${fact.version}`}><div className="entity-fact-heading"><strong>{fact.predicate}</strong><span>v{fact.version}</span></div><p>{fact.value || fact.content}</p>
        <details><summary>{fact.evidence.length} 条原始证据</summary>{fact.evidence.map((evidence, i) => <blockquote key={i}>{evidence.quote}</blockquote>)}<small>作用范围：{entity.scope_id}</small></details>
        <div className="entity-actions"><button className="text-command" disabled={busy} onClick={() => onHistory(fact.memory_id)}>查看演化历史</button><button className="text-command" disabled={busy} onClick={() => onEvolve(fact)}>审阅 / 撤回</button></div>
      </div>)}
    </details>
    {!!entity.pending_facts.length && <details className="entity-pending"><summary>{entity.pending_facts.length} 条待确认补充</summary>{entity.pending_facts.map((fact) => <div key={`${fact.memory_id}:${fact.version}`}><p>{fact.content}</p><button className="text-command" disabled={busy} onClick={() => onEvolve(fact)}>审阅待确认信息</button></div>)}</details>}
  </article>)}</div>;
}
