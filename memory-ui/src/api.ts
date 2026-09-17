import type {
  AnswerResult,
  EvolutionInput,
  Health,
  Hierarchy,
  HistoryEntry,
  Memory,
  Session,
  SessionSnapshot,
  SummaryGroup,
  MaintenanceReport,
} from "./types";
import type { RunEvent } from "./RunCircuit";

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
  answerStream: async (
    sessionId: string,
    query: string,
    topK: number,
    accelerate: boolean,
    onStage: (event: RunEvent) => void,
    onFallback: () => void,
  ): Promise<AnswerResult> => {
    const response = await fetch(
      `${API_BASE}/api/v1/sessions/${sessionId}/answer/stream`,
      {
        method: "POST",
        signal: AbortSignal.timeout(190_000),
        headers: {
          "Content-Type": "application/json",
          ...(getServiceToken()
            ? { Authorization: `Bearer ${getServiceToken()}` }
            : {}),
        },
        body: JSON.stringify({
          session_id: sessionId,
          query,
          top_k: topK,
          timeout: 180,
          accelerate,
        }),
      },
    );
    if ([404, 405].includes(response.status)) {
      onFallback();
      return api.answer(sessionId, query, topK, accelerate);
    }
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new ApiError(
        response.status,
        typeof data?.detail === "string"
          ? data.detail
          : `请求失败 (${response.status})`,
      );
    }
    if (!response.body) throw new Error("服务没有返回阶段数据流");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let result: AnswerResult | null = null;
    const consume = (line: string) => {
      if (!line.trim()) return;
      const event = JSON.parse(line);
      if (
        event.type === "stage" &&
        typeof event.phase === "string" &&
        typeof event.detail === "string" &&
        Number.isFinite(event.elapsed_ms)
      )
        onStage(event);
      else if (event.type === "result" && event.answer) result = event.answer;
      else if (event.type === "error")
        throw new Error(
          typeof event.detail === "string"
            ? event.detail
            : "本次运行失败，请查看服务日志",
        );
    };
    try {
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        lines.forEach(consume);
        if (done) {
          consume(buffer);
          break;
        }
      }
    } finally {
      await reader.cancel().catch(() => undefined);
      reader.releaseLock();
    }
    if (!result)
      throw new Error("阶段连接已结束，但未收到完整回答。请重试已保存的问题。");
    return result;
  },
  memories: () => request<Memory[]>("/api/v1/memories"),
  hierarchy: () => request<Hierarchy>("/api/v1/hierarchy"),
  groups: () =>
    request<SummaryGroup[]>("/api/v1/groups").catch((error) => {
      if (error instanceof ApiError && error.status === 404) return [];
      throw error;
    }),
  maintain: () =>
    request<MaintenanceReport>("/api/v1/maintenance", { method: "POST" }),
  history: (id: string) =>
    request<HistoryEntry[]>(`/api/v1/memories/${id}/history`),
  evolve: (memory: Memory, input: EvolutionInput) =>
    request<Memory>(`/api/v1/memories/${memory.memory_id}/evolve`, {
      method: "POST",
      body: JSON.stringify({
        expected_version: memory.version,
        expected_revision: memory.revision,
        ...input,
      }),
    }),
};
