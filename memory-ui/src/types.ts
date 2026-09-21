export type Role = "user" | "assistant" | "tool";
export interface EvolutionInput {
  action:
    | "promote"
    | "merge"
    | "supersede"
    | "correct"
    | "retract"
    | "defer"
    | "archive"
    | "restore"
    | "activate";
  target_id?: string;
  target_version?: number;
  target_revision?: number;
  effective_at?: string;
}
export interface HistoryEntry {
  action: string;
  recorded_at: string;
  before_json: string | null;
  after_json: string;
}

export interface Health {
  status: string;
  storage: string;
  model_configured: boolean;
  model_name: string | null;
  model_provider: string | null;
  semantic_retrieval: boolean;
  scope_mode: string;
  host_sdk: string;
}

export interface EventItem {
  role: Role;
  content: string;
  occurred_at: string;
  answer_id?: string | null;
  answer_context?: AnswerContext | null;
}

export interface Turn {
  turn_id: string;
  session_id: string;
  sequence: number;
  request_id: string;
  events: EventItem[];
}

export interface Session {
  session_id: string;
  topic: string;
  project_id: string | null;
  goal: string;
  plan: string[];
  pending_tasks: string[];
  summary: string;
  processed_sequence: number;
  revision: number;
  created_at: string;
  updated_at?: string;
}

export interface Evidence {
  turn_id: string;
  event_index: number;
  quote: string;
}

export interface Memory {
  memory_id: string;
  version: number;
  revision?: number;
  session_id: string;
  tier: "short" | "long";
  status: "active" | "superseded" | "retracted" | "pending" | "archived";
  kind: "fact" | "preference" | "event" | "procedure";
  content: string;
  subject: string;
  predicate: string;
  value: string;
  keywords: string[];
  evidence: Evidence[];
  assertion: string;
  durable: boolean;
  scope_type: "session" | "project" | "user";
  scope_id: string;
  hierarchy_path: string[];
  valid_from: string | null;
  valid_to: string | null;
  recorded_at: string;
}

export interface SessionSnapshot {
  session: Session;
  recent_turns: Turn[];
  short_memories: Memory[];
  pending_turns: number;
}

export interface QueryPlan {
  route: "none" | "short" | "long" | "both";
  semantic_queries: string[];
  keywords: string[];
  required_info: string[];
  depth: number;
  subject: string | null;
  predicate: string | null;
  temporal_mode: "current" | "history";
  as_of: string | null;
}

export interface QueryStep {
  order: number;
  phase: string;
  action: string;
  input_count: number;
  output_count: number;
  detail: string;
}

export interface Hit {
  content: string;
  score: number;
  source_file: string;
  chunk_id: string;
  engine: string;
  metadata: Record<string, unknown>;
}

export interface ChannelCandidate {
  memory_id: string;
  version: number;
  revision: number;
  subject: string;
  predicate: string;
  value: string;
  content: string;
  content_truncated?: boolean;
  matched: boolean;
  selected: boolean;
  score: number | null;
  reason: string;
}
export interface RetrievalChannel {
  view: "semantic" | "lexical" | "symbolic";
  tier: "short" | "long";
  status: "complete" | "disabled" | "skipped";
  input_count: number;
  matched_count: number;
  selected_count: number;
  elapsed_ms?: number;
  detail: string;
  query_conditions?: Record<string, unknown>;
  candidates?: ChannelCandidate[];
  candidate_total?: number;
  candidate_limit?: number;
  candidates_truncated?: boolean;
}
export interface DynamicK {
  planned_depth: number;
  required_info_count: number;
  candidate_limit: number;
  safety_cap: number;
  target_k: number;
  selected_k: number;
  token_limit: number;
  used_tokens: number;
  selection_policy: string;
  score_semantics: string;
}
export interface AnswerContext {
  sources: Hit[];
  citations: number[];
  query_steps: QueryStep[];
  plan: QueryPlan | null;
  elapsed_ms: number;
  retrieval_mode: string;
  candidate_count: number;
  selected_k: number;
  channels?: RetrievalChannel[];
  dynamic_k?: DynamicK | null;
  acceleration?: Acceleration | null;
  run_events: import("./RunCircuit").RunEvent[];
}

export interface AnswerResult {
  answer_id?: string;
  generated_text: string;
  citations: number[];
  sources: Hit[];
  retrieval_count: number;
  elapsed_ms: number;
  plan: QueryPlan | null;
  query_steps: QueryStep[];
  retrieval_mode: string;
  candidate_count: number;
  selected_k: number;
  context_tokens: number;
  channels?: RetrievalChannel[];
  dynamic_k?: DynamicK | null;
  warnings: string[];
  acceleration?: Acceleration;
  memory_updates?: Array<{
    candidate_count?: number;
    evolution?: EvolutionReport;
  }>;
}

export interface EvolutionReport {
  applied: Array<{
    action: string;
    memory_id: string;
    source_id?: string;
    target_id?: string;
    version?: number;
  }>;
  review: Array<{
    memory_id: string;
    reason?: string;
    target_ids?: string[];
    recommendation?: string;
    strength?: number;
    model_proposal?: {
      relation: string;
      reason: string;
      evidence_quotes: string[];
    };
  }>;
  warnings?: string[];
}
export interface SummaryGroup {
  path: string[];
  level: number;
  scope_type: string;
  scope_id: string;
  mode: string;
  summary: string;
  memory_refs: string[];
  omitted_count?: number;
  summary_items?: Array<{
    text: string;
    memory_ref: string;
    assertion: string;
    evidence_count: number;
  }>;
  synthesis?: {
    summary: string;
    source_refs: string[];
    coverage_count?: number;
    group_count?: number;
  };
}
export interface MaintenanceReport extends EvolutionReport {
  retention: Array<{
    memory_id: string;
    strength: number;
    age_days: number;
    recommendation: string;
    evidence_reinforcement: number;
  }>;
  group_count: number;
}

export interface Acceleration {
  enabled: boolean;
  cache_hit: boolean;
  cache_status: "disabled" | "miss" | "hit" | "shared";
  strategy: string;
  retrieval_ms: number;
  generation_ms: number;
  total_ms: number;
  context_tokens_before: number;
  context_tokens_after: number;
  avoided_model_calls: number;
  original_generation_ms: number | null;
}

export interface TraceNode {
  name: string;
  episode_count: number;
  active_count: number;
  episodes: Array<{
    memory_id: string;
    version: number;
    content: string;
    kind: string;
    status: string;
    scope_type: string;
    scope_id: string;
    valid_from: string | null;
    valid_to: string | null;
    recorded_at: string;
    evidence_count: number;
    source_session_id: string;
  }>;
}

export interface CategoryNode {
  name: string;
  episode_count: number;
  traces: TraceNode[];
}

export interface DomainNode {
  name: string;
  episode_count: number;
  categories: CategoryNode[];
}

export interface Hierarchy {
  schema: string;
  organization_mode: string;
  summary_mode: string;
  domain_count: number;
  episode_count: number;
  domains: DomainNode[];
}

export interface EntityMemory {
  entity_id: string;
  subject: string;
  scope_type: string;
  scope_id: string;
  summary: string;
  facts: Memory[];
  pending_facts: Memory[];
  conflict_predicates: string[];
  updated_at: string;
}
