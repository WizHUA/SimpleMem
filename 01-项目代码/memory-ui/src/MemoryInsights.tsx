import type {
  AnswerResult,
  MaintenanceReport,
  Memory,
  SummaryGroup,
} from "./types";

const ACTIONS: Record<string, string> = {
  promote: "晋升长期",
  merge: "合并证据",
  supersede: "替代旧版本",
  correct: "纠正错误事实",
  archive: "归档",
  restore: "恢复",
  defer: "待确认",
  activate: "激活",
};
const REASONS: Record<string, string> = {
  conflicting_values_require_review: "同一属性存在不同值，需要人工审阅",
  no_user_evidence: "缺少直接用户证据",
  review_archive: "保留强度较低，建议审阅是否归档",
  archive_expired_event: "事件已过期，可恢复归档",
  keep: "继续保留",
};

export function MemoryChanges({
  result,
  memories,
  onReview,
}: {
  result: AnswerResult | null;
  memories: Memory[];
  onReview: (memory: Memory) => void;
}) {
  const updates = result?.memory_updates || [];
  if (!updates.length) return null;
  const applied = updates.flatMap((item) => item.evolution?.applied || []);
  const reviews = updates.flatMap((item) => item.evolution?.review || []);
  const warnings = updates.flatMap((item) => item.evolution?.warnings || []);
  return (
    <section className="memory-changes" aria-label="本次记忆变化">
      <h3>回答前，记忆发生了什么</h3>
      <p>
        抽取{" "}
        {updates.reduce(
          (total, item) => total + (item.candidate_count || 0),
          0,
        )}{" "}
        条候选，应用 {applied.length} 项演化，{reviews.length} 项待审阅。
      </p>
      {applied.length > 0 && (
        <div className="change-actions">
          {applied.map((item, index) => (
            <span key={index}>
              {ACTIONS[item.action] || item.action}
              {item.version ? ` · v${item.version}` : ""}
            </span>
          ))}
        </div>
      )}
      {reviews.map((item, index) => {
        const memory = memories.find(
          (entry) => entry.memory_id === item.memory_id,
        );
        return (
          <div className="review-request" key={index}>
            <strong>{memory?.content || item.memory_id}</strong>
            <p>{REASONS[item.reason || ""] || item.reason}</p>
            {item.model_proposal && (
              <details>
                <summary>查看辅助判断（未自动采纳）</summary>
                <p>{item.model_proposal.reason}</p>
                {item.model_proposal.evidence_quotes.map((quote) => (
                  <blockquote key={quote}>{quote}</blockquote>
                ))}
              </details>
            )}
            {memory && (
              <button type="button" onClick={() => onReview(memory)}>
                审阅这条候选
              </button>
            )}
          </div>
        );
      })}
      {warnings.map((warning, index) => (
        <p key={index} className="measurement-note">
          {warning}
        </p>
      ))}
    </section>
  );
}

export function LongTermInsights({
  groups,
  report,
  memories,
  busy,
  onMaintain,
  onReview,
  onHistory,
}: {
  groups: SummaryGroup[];
  report: MaintenanceReport | null;
  memories: Memory[];
  busy: boolean;
  onMaintain: () => void;
  onReview: (memory: Memory) => void;
  onHistory: (id: string) => void;
}) {
  const pending = memories.filter((memory) => memory.status === "pending");
  return (
    <section className="long-insights">
      {pending.length > 0 && (
        <details className="review-queue" open>
          <summary>待确认候选 · {pending.length}</summary>
          <p>
            这些候选尚未作为有效事实参与检索。核对原文后，再选择激活、替代或撤回。
          </p>
          {pending.map((memory) => (
            <div className="review-request" key={memory.memory_id}>
              <strong>{memory.content}</strong>
              <p>
                {memory.subject} / {memory.predicate} · {memory.scope_type}
              </p>
              <details>
                <summary>核对原文证据</summary>
                {memory.evidence.map((evidence, index) => (
                  <blockquote key={index}>{evidence.quote}</blockquote>
                ))}
              </details>
              <button
                type="button"
                disabled={busy}
                onClick={() => onReview(memory)}
              >
                审阅这条候选
              </button>
            </div>
          ))}
        </details>
      )}
      <details className="summary-groups">
        <summary>
          分层摘要与记忆维护 <span>{groups.length} 个分组</span>
        </summary>
        <p>
          摘要是带来源的派生视图，不会作为新事实或抽取证据写回。维护会合并重复事实、归档过期事件并更新摘要，可能调用模型。
        </p>
        <button
          className="command-button"
          type="button"
          disabled={busy}
          onClick={onMaintain}
        >
          运行记忆维护
        </button>
        {groups.length === 0 && (
          <p className="empty-text">
            尚无分层摘要，可在长期记忆形成后运行维护。
          </p>
        )}
        {groups.map((group) => (
          <details
            className="summary-group"
            key={`${group.scope_type}:${group.scope_id}:${group.path.join("/")}`}
          >
            <summary>
              <span className="summary-level">第 {group.level} 层</span>
              {group.path.join(" / ")}
            </summary>
            <p className="summary-scope">
              {group.scope_type} / {group.scope_id} · {group.memory_refs.length}{" "}
              个记忆版本
            </p>
            {group.synthesis && (
              <div className="synthesis">
                <strong>模型辅助摘要 · 派生视图</strong>
                <p>{group.synthesis.summary}</p>
                <small>
                  引用 {group.synthesis.source_refs.length} 个版本
                  {group.synthesis.coverage_count !== undefined
                    ? `，处理 ${group.synthesis.coverage_count}/${group.synthesis.group_count} 条记忆`
                    : ""}
                </small>
                <div className="summary-source-links">
                  {group.synthesis.source_refs.map((ref) => (
                    <button
                      type="button"
                      key={ref}
                      onClick={() =>
                        onHistory(ref.slice(0, ref.lastIndexOf(":")))
                      }
                      disabled={busy}
                    >
                      {ref}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {group.summary_items?.length ? (
              <ul>
                {group.summary_items.map((item) => (
                  <li key={item.memory_ref}>
                    <span>{item.text}</span>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        onHistory(
                          item.memory_ref.slice(
                            0,
                            item.memory_ref.lastIndexOf(":"),
                          ),
                        )
                      }
                    >
                      {item.evidence_count} 条证据 · 查看版本
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p>{group.summary}</p>
            )}
            {!!group.omitted_count && (
              <small>
                长度预算内省略 {group.omitted_count}{" "}
                条，完整事实请查看记忆目录。
              </small>
            )}
          </details>
        ))}
        {report && (
          <div className="maintenance-result" aria-label="维护结果">
            <h3>本次维护结果</h3>
            <p>
              应用 {report.applied.length} 项变更，{report.review.length}{" "}
              项建议待审阅，生成 {report.group_count} 个分组。
            </p>
            {report.applied.map((item, index) => (
              <p key={index}>
                {ACTIONS[item.action] || item.action} ·{" "}
                {memories.find((memory) => memory.memory_id === item.memory_id)
                  ?.content || item.memory_id}
              </p>
            ))}
            <details>
              <summary>保留强度与归档建议</summary>
              <p>
                强度来自记忆年龄与证据次数，不是事实可信度；不会自动删除稳定事实。
              </p>
              {report.retention.map((item) => {
                const memory = memories.find(
                  (entry) => entry.memory_id === item.memory_id,
                );
                return (
                  <div className="retention-row" key={item.memory_id}>
                    <span>{memory?.content || item.memory_id}</span>
                    <meter
                      min={0}
                      max={1}
                      value={item.strength}
                      aria-label="年龄与证据保留强度"
                    />
                    <small>
                      {Math.round(item.strength * 100)}% ·{" "}
                      {item.age_days.toFixed(1)} 天 ·{" "}
                      {REASONS[item.recommendation] || item.recommendation}
                    </small>
                    {memory && item.recommendation !== "keep" && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => onReview(memory)}
                      >
                        审阅归档建议
                      </button>
                    )}
                  </div>
                );
              })}
            </details>
            {report.warnings?.map((warning, index) => (
              <p key={index}>{warning}</p>
            ))}
          </div>
        )}
      </details>
    </section>
  );
}
