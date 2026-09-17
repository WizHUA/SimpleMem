import type {
  AnswerResult,
  EvolutionInput,
  Health,
  Hierarchy,
  HistoryEntry,
  Memory,
  Session,
  SessionSnapshot,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
export const apiBase = API_BASE || "同源记忆服务";
export const getServiceToken = () =>
  sessionStorage.getItem("memory-service-token") || "";
export const setServiceToken = (token: string) =>
  token
    ? sessionStorage.setItem("memory-service-token", token.trim())
    : sessionStorage.removeItem("memory-service-token");

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    signal: AbortSignal.timeout(190_000),
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(getServiceToken()
        ? { Authorization: `Bearer ${getServiceToken()}` }
        : {}),
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const detail = data?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail
              .map((item: { msg?: string }) => item.msg || "输入格式不正确")
              .join("；")
          : `请求失败 (${response.status})`;
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<Health>("/health"),
  createSession: (topic: string, projectId: string) =>
    request<Session>("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({ topic, project_id: projectId }),
    }),
  session: (id: string) => request<SessionSnapshot>(`/api/v1/sessions/${id}`),
  append: (
    sessionId: string,
    role: "user" | "assistant" | "tool",
    content: string,
    requestId: string = crypto.randomUUID(),
  ) =>
    request<{
      extraction_due: boolean;
      extraction_reasons: string[];
      pending_turns: number;
    }>(`/api/v1/sessions/${sessionId}/turns`, {
      method: "POST",
      body: JSON.stringify({
        request_id: requestId,
        // Let the server timestamp the event so retries retain an identical payload.
        events: [{ role, content }],
      }),
    }),
  extract: (sessionId: string) =>
    request<{
      status: string;
      processed_sequence: number;
      summary: string;
      candidate_count: number;
      memories: Memory[];
    }>(`/api/v1/sessions/${sessionId}/extract`, {
      method: "POST",
    }),
  answer: (sessionId: string, query: string, topK: number, accelerate = true) =>
    request<AnswerResult>("/api/v1/answer", {
      method: "POST",
      body: JSON.stringify({
        session_id: sessionId,
        query,
        top_k: topK,
        timeout: 180,
        accelerate,
      }),
    }),
  memories: () => request<Memory[]>("/api/v1/memories"),
  hierarchy: () => request<Hierarchy>("/api/v1/hierarchy"),
  history: (id: string) =>
    request<HistoryEntry[]>(`/api/v1/memories/${id}/history`),
  evolve: (memory: Memory, input: EvolutionInput) =>
    request<Memory>(`/api/v1/memories/${memory.memory_id}/evolve`, {
      method: "POST",
      body: JSON.stringify({ expected_version: memory.version, ...input }),
    }),
};
