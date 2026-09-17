import { expect, test, type Page } from "@playwright/test";

async function memoryBackend(
  page: Page,
  options: {
    failFirstAnswer?: boolean;
    failFirstHealth?: boolean;
    failFirstAssistantAck?: boolean;
  } = {},
) {
  const state = {
    events: [] as any[],
    answers: [] as any[],
    extracted: false,
    promoted: false,
    healthCalls: 0,
    assistantAttempts: [] as string[],
    sessions: 0,
  };
  const session = {
    session_id: "session-demo-123456",
    topic: "记忆系统课程演示",
    project_id: "memory-system-demo",
    goal: "交付可追溯记忆",
    plan: [],
    pending_tasks: [],
    summary: "",
    processed_sequence: 0,
    revision: 1,
    created_at: "2026-09-17T09:00:00Z",
  };
  const memory = {
    memory_id: "memory-001",
    version: 1,
    session_id: session.session_id,
    tier: "short",
    status: "active",
    kind: "preference",
    content: "用户偏好中文说明",
    subject: "用户",
    predicate: "偏好语言",
    value: "中文",
    keywords: ["中文"],
    evidence: [{ turn_id: "turn-1", event_index: 0, quote: "我偏好中文说明" }],
    assertion: "confirmed",
    durable: true,
    scope_type: "user",
    scope_id: "local-user",
    hierarchy_path: ["用户", "偏好", "语言"],
    valid_from: null,
    valid_to: null,
    recorded_at: "2026-09-17T09:00:00Z",
  };
  await page.route(/\/(health|api\/v1\/.*)$/, async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    const body = route.request().postDataJSON();
    const json = (data: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(data),
      });
    if (path === "/health") {
      state.healthCalls++;
      if (options.failFirstHealth && state.healthCalls === 1)
        return json({ detail: "后端暂时不可用" }, 503);
      return json({
        status: "ok",
        storage: "sqlite",
        model_configured: true,
        model_name: "课程测试模型",
        model_provider: "test-fixture",
        semantic_retrieval: false,
        scope_mode: "本地单用户",
        host_sdk: "adapter",
      });
    }
    if (path === "/api/v1/sessions" && method === "POST") {
      state.sessions++;
      return json(session, 201);
    }
    if (path.endsWith("/turns")) {
      if (body.events[0].role === "assistant")
        state.assistantAttempts.push(body.request_id);
      const existing = state.events.find(
        (event) => event.request_id === body.request_id,
      );
      if (
        existing &&
        JSON.stringify(existing.requestBody) !== JSON.stringify(body)
      )
        return json(
          { detail: "request_id reused with different content" },
          409,
        );
      if (!existing)
        state.events.push({
          ...body,
          requestBody: body,
          events: body.events.map((event: object) => ({
            ...event,
            occurred_at: "2026-09-17T09:00:00Z",
          })),
          turn_id: `turn-${state.events.length + 1}`,
          sequence: state.events.length + 1,
          session_id: session.session_id,
        });
      if (options.failFirstAssistantAck && state.assistantAttempts.length === 1)
        return json({ detail: "回答已写入，但响应中断" }, 502);
      return json(
        {
          extraction_due: true,
          extraction_reasons: ["explicit"],
          pending_turns: state.events.length,
        },
        201,
      );
    }
    if (path.endsWith("/extract")) {
      state.extracted = true;
      session.summary = "用户偏好中文说明";
      session.processed_sequence = state.events.length;
      return json({
        status: "ok",
        processed_sequence: state.events.length,
        summary: session.summary,
        candidate_count: 1,
        memories: [memory],
      });
    }
    if (path.endsWith("/evolve")) {
      state.promoted = true;
      return json({ ...memory, tier: "long", version: 2 });
    }
    if (path === "/api/v1/memories")
      return json(state.extracted && !state.promoted ? [memory] : []);
    if (path === "/api/v1/hierarchy")
      return json({
        schema: "hmem-reference/v1",
        organization_mode: "domain_category_trace_episode",
        summary_mode: "labels",
        domain_count: state.promoted ? 1 : 0,
        episode_count: state.promoted ? 1 : 0,
        domains: state.promoted
          ? [
              {
                name: "用户",
                episode_count: 1,
                categories: [
                  {
                    name: "偏好",
                    episode_count: 1,
                    traces: [
                      {
                        name: "语言",
                        episode_count: 1,
                        active_count: 1,
                        episodes: [
                          {
                            ...memory,
                            version: 2,
                            evidence_count: 1,
                            source_session_id: session.session_id,
                          },
                        ],
                      },
                    ],
                  },
                ],
              },
            ]
          : [],
      });
    if (path === "/api/v1/answer") {
      state.answers.push(body);
      if (options.failFirstAnswer && state.answers.length === 1)
        return json({ detail: "模型服务响应超时，请重试。" }, 504);
      const hit =
        body.accelerate &&
        state.answers.filter((answer) => answer.accelerate).length > 1;
      return json({
        generated_text: "已记住你的偏好，将优先使用中文。【来源1】",
        citations: [1],
        sources: [
          {
            content: "用户偏好中文说明",
            score: 0.92,
            source_file: "memory-001",
            chunk_id: "memory-001:v1",
            engine: "symbolic",
            metadata: { scope_type: "user" },
          },
        ],
        retrieval_count: 1,
        elapsed_ms: hit ? 22 : 420,
        plan: {
          route: "both",
          semantic_queries: [body.query],
          keywords: ["中文"],
          required_info: ["语言偏好"],
          depth: 5,
          subject: "用户",
          predicate: null,
          temporal_mode: "current",
          as_of: null,
        },
        query_steps: [
          {
            order: 1,
            phase: "retrieval",
            action: "三视图检索",
            input_count: 3,
            output_count: 1,
            detail: "符号、全文、语义候选合并后筛选",
          },
        ],
        retrieval_mode: "hybrid",
        candidate_count: 3,
        selected_k: 1,
        context_tokens: 80,
        warnings: [],
        acceleration: {
          enabled: body.accelerate,
          cache_hit: hit,
          cache_status: body.accelerate ? (hit ? "hit" : "miss") : "disabled",
          strategy: "exact_context_generation_cache",
          retrieval_ms: 20,
          generation_ms: hit ? 2 : 400,
          total_ms: hit ? 22 : 420,
          context_tokens_before: 100,
          context_tokens_after: 80,
          avoided_model_calls: hit ? 1 : 0,
          original_generation_ms: hit ? 400 : null,
        },
      });
    }
    if (path.startsWith("/api/v1/sessions/"))
      return json({
        session,
        recent_turns: state.events,
        short_memories: state.extracted && !state.promoted ? [memory] : [],
        pending_turns: state.events.length - session.processed_sequence,
      });
    return json({ detail: `Unmocked: ${method} ${path}` }, 500);
  });
  return state;
}

async function send(page: Page) {
  await page.getByLabel("输入消息", { exact: true }).fill("我偏好中文说明");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
}

test("真实数据契约：对话、原文证据、晋升与来源追踪", async ({ page }) => {
  const state = await memoryBackend(page);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "记忆系统课程演示" }),
  ).toBeVisible();
  await send(page);
  await expect(page.getByRole("heading", { name: "意图与查询" })).toBeVisible();
  await expect(page.getByText("三视图检索", { exact: true })).toBeVisible();
  expect(state.events).toHaveLength(2);
  await page.getByRole("button", { name: "短期", exact: true }).click();
  await page.getByRole("button", { name: "整理", exact: true }).click();
  await page.getByText("1 条原文证据 · v1").click();
  await expect(page.locator("blockquote")).toContainText("我偏好中文说明");
  await page.getByRole("button", { name: "晋升长期", exact: true }).click();
  await page.getByRole("button", { name: "确认晋升长期", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "H-MEM 长期组织" }),
  ).toBeVisible();
  await expect(page.locator(".episode")).toContainText("用户偏好中文说明");
  await page.screenshot({
    path: "test-results/desktop-memory-lifecycle.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("回答失败后仅重试回答，避免重复写入用户事件", async ({ page }) => {
  const state = await memoryBackend(page, { failFirstAnswer: true });
  await page.goto("/");
  await send(page);
  await expect(page.getByRole("alert")).toContainText("模型服务响应超时");
  await page.getByRole("button", { name: "重试已保存的问题" }).click();
  await expect(page.locator(".message-assistant")).toHaveCount(1);
  expect(state.events).toHaveLength(2);
  expect(state.answers).toHaveLength(2);
});

test("回答写入响应中断后复用生成结果与幂等键", async ({ page }) => {
  const state = await memoryBackend(page, { failFirstAssistantAck: true });
  await page.goto("/");
  await send(page);
  await expect(page.getByRole("alert")).toContainText("响应中断");
  await page.getByRole("button", { name: "重试已保存的问题" }).click();
  await expect(page.locator(".message-assistant")).toHaveCount(1);
  expect(state.events).toHaveLength(2);
  expect(state.answers).toHaveLength(1);
  expect(state.assistantAttempts[0]).toBe(state.assistantAttempts[1]);
});

test("加速观测对照不污染会话，按接口返回展示命中与耗时", async ({ page }) => {
  const state = await memoryBackend(page);
  await page.goto("/");
  await send(page);
  await expect(page.locator(".message-assistant")).toHaveCount(1);
  await page.getByRole("button", { name: "观测", exact: true }).click();
  await page.getByRole("button", { name: "运行无缓存基线" }).click();
  await expect(page.locator(".cache-state")).toContainText("已关闭");
  await page.getByRole("button", { name: "运行加速查询" }).click();
  await expect(page.locator(".cache-state")).toContainText("命中精确上下文");
  await expect(page.locator(".cache-state")).toContainText("少调用 1 次");
  expect(state.events).toHaveLength(2);
  expect(state.answers.map((answer) => answer.accelerate)).toEqual([
    true,
    false,
    true,
  ]);
  await expect(page.locator(".comparison-table")).toContainText("420 ms");
  await expect(page.locator(".comparison-table")).toContainText("22 ms");
  await page.screenshot({
    path: "test-results/desktop-acceleration.png",
    fullPage: true,
  });
});

test("离线错误可恢复，窄屏无溢出，新会话对话框支持键盘", async ({ page }) => {
  const state = await memoryBackend(page, { failFirstHealth: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("后端暂时不可用");
  await page.getByRole("button", { name: "重新连接并刷新" }).click();
  await expect(
    page.getByRole("heading", { name: "记忆系统课程演示" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.getByRole("button", { name: "新会话", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByLabel("会话主题", { exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  expect(state.sessions).toBe(1);
  await page.screenshot({
    path: "test-results/mobile-empty-state.png",
    fullPage: true,
  });
});

test("中文输入法确认键不会误发送，Shift+Enter 保留换行", async ({ page }) => {
  const state = await memoryBackend(page);
  await page.goto("/");
  const input = page.getByLabel("输入消息", { exact: true });
  await input.fill("中文输入法测试");
  await input.dispatchEvent("keydown", {
    key: "Enter",
    code: "Enter",
    isComposing: true,
  });
  expect(state.events).toHaveLength(0);
  await input.press("Shift+Enter");
  await expect(input).toHaveValue("中文输入法测试\n");
  await input.press("Enter");
  await expect(page.locator(".message-assistant")).toHaveCount(1);
});
