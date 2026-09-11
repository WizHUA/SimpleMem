export type Role = "user" | "assistant" | "tool";

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
}

export interface Evidence {
  turn_id: string;
  event_index: number;
  quote: string;
}

export interface Memory {
  memory_id: string;
  version: number;
  session_id: string;
  tier: "short" | "long";
  status: "active" | "superseded" | "retracted" | "pending";
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

export interface AnswerResult {
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
  warnings: string[];
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
