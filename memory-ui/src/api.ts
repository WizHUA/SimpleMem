import type { AnswerResult, Health, Hierarchy, Memory, Session, SessionSnapshot } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8088").replace(/\/$/, "");

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new ApiError(response.status, data?.detail || `请求失败 (${response.status})`);
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
  append: (sessionId: string, role: "user" | "assistant" | "tool", content: string) =>
    request<{ extraction_due: boolean; extraction_reasons: string[]; pending_turns: number }>(
      `/api/v1/sessions/${sessionId}/turns`,
      {
        method: "POST",
        body: JSON.stringify({
          request_id: crypto.randomUUID(),
          events: [{ role, content, occurred_at: new Date().toISOString() }],
        }),
      },
    ),
  extract: (sessionId: string) =>
    request<{ status: string; memories: Memory[] }>(`/api/v1/sessions/${sessionId}/extract`, {
      method: "POST",
    }),
  answer: (sessionId: string, query: string, topK: number) =>
    request<AnswerResult>("/api/v1/answer", {
      method: "POST",
      body: JSON.stringify({ session_id: sessionId, query, top_k: topK, timeout: 180 }),
    }),
  memories: () => request<Memory[]>("/api/v1/memories"),
  hierarchy: () => request<Hierarchy>("/api/v1/hierarchy"),
  promote: (memory: Memory) =>
    request<Memory>(`/api/v1/memories/${memory.memory_id}/evolve`, {
      method: "POST",
      body: JSON.stringify({ action: "promote", expected_version: memory.version }),
    }),
};
