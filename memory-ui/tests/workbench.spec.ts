import { expect, test, type Page } from "@playwright/test";

async function memoryBackend(
  page: Page,
  options: {
    failFirstAnswer?: boolean;
    failFirstHealth?: boolean;
    failFirstAssistantAck?: boolean;
    stageStream?: boolean;
    streamError?: boolean;
    missingResultWithReceipt?: boolean;
    readableTrace?: boolean;
    citationScenario?: boolean;
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
    receipts: {} as Record<string, any>,
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
    const json = (data: any, status = 200) => {
      if (data.generated_text) {
        data.answer_id = `answer-${state.answers.length}`;
        state.receipts[data.answer_id] = { ...data, run_events: [] };
      }
      if (path.endsWith("/answer/stream") && status === 200) {
        const stages = [
          {
            type: "stage",
            phase: "extraction",
            detail: "已核验新事件，疑问不确认为事实",
            elapsed_ms: 5,
          },
          {
            type: "stage",
            phase: "planning",
            detail: "规划用户语言偏好查询",
            elapsed_ms: 40,
          },
          {
            type: "stage",
            phase: "retrieval",
            detail: "从短期与长期召回候选",
            elapsed_ms: 50,
          },
          {
            type: "stage",
            phase: "generation",
            detail: "使用入选证据生成回答",
            elapsed_ms: 55,
          },
          ...(options.streamError
            ? [{ type: "error", detail: "生成阶段连接失败" }]
            : options.missingResultWithReceipt
              ? [
                  {
                    type: "stage",
                    phase: "completed",
                    detail: "回答与来源已保存",
                    elapsed_ms: 420,
                  },
                  { type: "receipt", answer_id: data.answer_id },
                ]
            : [
                {
                  type: "stage",
                  phase: "completed",
                  detail: "回答与来源已返回",
                  elapsed_ms: 420,
                },
                { type: "receipt", answer_id: data.answer_id },
                { type: "result", answer: data },
              ]),
        ];
        state.receipts[data.answer_id].run_events = stages.filter((event) => event.type === "stage");
        return route.fulfill({
          status: 200,
          contentType: "application/x-ndjson",
          body: stages.map((event) => JSON.stringify(event)).join("\n") + "\n",
        });
      }
      return route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(data),
      });
    };
    if (path.endsWith("/answer/stream") && !options.stageStream)
      return json({ detail: "Endpoint unavailable" }, 404);
    if (path === "/api/v1/entities") return json({ detail: "legacy fixture" }, 404);
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
          events: body.events.map((event: any) => ({
            ...event,
            answer_context: event.answer_id ? state.receipts[event.answer_id] : null,
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
    if (path.includes("/answers/")) {
      const answerId = path.split("/").pop() || "";
      return state.receipts[answerId]
        ? json(state.receipts[answerId])
        : json({ detail: "missing receipt" }, 404);
    }
    if (path === "/api/v1/memories")
      return json(state.extracted && !state.promoted ? [memory] : []);
    if (path === "/api/v1/groups") return json([]);
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
    if (path === "/api/v1/answer" || path.endsWith("/answer/stream")) {
      state.answers.push(body);
      if (options.failFirstAnswer && state.answers.length === 1)
        return json({ detail: "模型服务响应超时，请重试。" }, 504);
      const hit =
        body.accelerate &&
        state.answers.filter((answer) => answer.accelerate).length > 1;
      return json({
        generated_text: options.citationScenario ? `第 ${state.answers.length} 轮：是的，zfc 就是飞猪。【来源1】【来源2】\n\n代码保留：\`【来源1】\`，链接保留：[【来源2】](https://example.com)。缺失引用【来源99】。` : "已记住你的偏好，将优先使用中文。【来源1】",
        citations: options.citationScenario ? [1, 2] : [1],
        sources: [
          {
            content: options.citationScenario ? `第 ${state.answers.length} 轮的昵称记忆：zfc 是飞猪` : "用户偏好中文说明",
            score: 0.92,
            source_file: "memory-001",
            chunk_id: "memory-001:v1",
            engine: "symbolic",
            metadata: {
              ...memory,
              scope_type: "user",
              ...(options.readableTrace
                ? {
                    matched_views: [
                      "lexical",
                      "entity_link_support",
                      "new_channel",
                    ],
                  }
                : {}),
            },
          },
          ...(options.citationScenario ? [{ content: `第 ${state.answers.length} 轮的项目记忆`, score: 0.85, source_file: "memory-002", chunk_id: "memory-002:v2", engine: "symbolic", metadata: { ...memory, tier: "long", version: 2, scope_type: "project", evidence: [{ turn_id: "turn-project", event_index: 0, quote: "请长期记住项目代号是蓝鲸" }] } }] : []),
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
            action: options.readableTrace
              ? "semantic_lexical_symbolic_recall"
              : "三视图检索",
            input_count: 3,
            output_count: 1,
            detail: "符号、全文、语义候选合并后筛选",
          },
        ],
        retrieval_mode: options.readableTrace ? "lexical_baseline" : "hybrid",
        candidate_count: 3,
        selected_k: 1,
        context_tokens: 80,
        warnings: options.readableTrace
          ? [
              "multi_slot_recall: symbolic labels do not restrict other required fields",
              "fts5_projection: reranked=3; eligible=8",
              "long_term_searched",
              "short_evidence_uncertain: one long-term supplement performed",
              "required_info_coverage_unverified: retrieval similarity is not entailment",
              "future_diagnostic: custom opaque payload",
            ]
          : [],
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
  await page.getByRole("button", { name: "检索", exact: true }).click();
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

test("回答失败后再次发送同文问题不会重复写入用户事件", async ({ page }) => {
  const state = await memoryBackend(page, { failFirstAnswer: true });
  await page.goto("/");
  await send(page);
  await expect(page.getByRole("alert")).toContainText("模型服务响应超时");
  await send(page);
  await expect(page.locator(".message-assistant")).toHaveCount(1);
  expect(state.events).toHaveLength(2);
  expect(state.events.filter((event) => event.events[0].role === "user")).toHaveLength(1);
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

test("真实阶段协议：运行轨迹、显式回放和场景填入", async ({ page }) => {
  const state = await memoryBackend(page, { stageStream: true });
  await page.goto("/");
  await expect(page.getByLabel("输入消息", { exact: true })).toBeEnabled();
  await page.locator(".scenario-guide > summary").click();
  await page
    .getByRole("button", {
      name: "zfc 的昵称是飞猪。这里的飞猪只是我朋友的昵称。",
      exact: true,
    })
    .click();
  await expect(page.getByLabel("输入消息", { exact: true })).toHaveValue(
    "zfc 的昵称是飞猪。这里的飞猪只是我朋友的昵称。",
  );
  expect(state.events).toHaveLength(0);
  await page.locator(".scenario-guide > summary").click();
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await page.locator(".process-toggle").click();
  await expect(page.locator(".process-events li")).toHaveCount(5);
  await expect(page.locator(".process-toggle")).toContainText("已完成检索与回答");
  await expect(page.locator(".process-summary")).toContainText("3 条候选 → 1 条入选证据");
  await page.getByRole("button", { name: "回放运行记录" }).click();
  await expect(page.locator(".process-toggle")).toContainText("记录回放 · 节奏已压缩");
  await page.getByRole("button", { name: "退出记录回放" }).click();
  await expect(page.locator(".process-toggle")).toContainText("已完成检索与回答");
  await page.screenshot({
    path: "test-results/memory-circuit-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel("输入消息", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "test-results/memory-circuit-mobile.png",
    fullPage: true,
  });
});

test("阶段中断保留已发生事实，不自动重发生成", async ({ page }) => {
  const state = await memoryBackend(page, {
    stageStream: true,
    streamError: true,
  });
  await page.goto("/");
  await send(page);
  await expect(page.getByRole("alert")).toContainText("生成阶段连接失败");
  await expect(page.locator(".process-toggle")).toContainText("运行中断");
  await page.locator(".process-toggle").click();
  await expect(page.locator(".process-events li")).toHaveCount(4);
  expect(state.answers).toHaveLength(1);
  expect(state.events).toHaveLength(1);
});

test("阶段流缺失最终结果时用 receipt 取回并保存回答", async ({ page }) => {
  const state = await memoryBackend(page, {
    stageStream: true,
    missingResultWithReceipt: true,
  });
  await page.goto("/");
  await send(page);
  await expect(page.locator(".message-assistant")).toContainText(
    "已记住你的偏好",
  );
  expect(state.answers).toHaveLength(1);
  expect(state.events).toHaveLength(2);
  expect(state.events[1].events[0].answer_id).toBe("answer-1");
});

test("阶段解析支持跨字节中文、分块行和无末尾换行", async ({ page }) => {
  await memoryBackend(page);
  await page.goto("/");
  const parsed = await page.evaluate(async () => {
    const { api } = await import("/src/api.ts");
    const payload = new TextEncoder().encode(
      JSON.stringify({
        type: "stage",
        phase: "planning",
        detail: "中文查询规划",
        elapsed_ms: 3,
      }) +
        "\n" +
        JSON.stringify({
          type: "result",
          answer: { generated_text: "完整回答" },
        }),
    );
    const originalFetch = window.fetch;
    window.fetch = async () =>
      new Response(
        new ReadableStream({
          start(controller) {
            for (let index = 0; index < payload.length; index += 5)
              controller.enqueue(payload.slice(index, index + 5));
            controller.close();
          },
        }),
        { headers: { "Content-Type": "application/x-ndjson" } },
      );
    const stages: any[] = [];
    try {
      const result = await api.answerStream(
        "test",
        "查询",
        true,
        (stage: any) => stages.push(stage),
        () => {
          throw new Error("unexpected fallback");
        },
      );
      return { result, stages };
    } finally {
      window.fetch = originalFetch;
    }
  });
  expect(parsed.stages[0].detail).toBe("中文查询规划");
  expect(parsed.result.generated_text).toBe("完整回答");
});

test("新增生命周期：冲突激活、归档恢复、分层摘要和保留建议", async ({
  page,
}) => {
  await memoryBackend(page);
  const base = {
    version: 1,
    revision: 4,
    session_id: "session-demo-123456",
    kind: "fact",
    subject: "项目",
    predicate: "数据库",
    value: "SQLite",
    keywords: [],
    evidence: [
      { turn_id: "turn-1", event_index: 0, quote: "项目数据库改为 SQLite" },
    ],
    assertion: "stated",
    durable: true,
    scope_type: "project",
    scope_id: "memory-system-demo",
    hierarchy_path: ["项目", "架构", "数据库"],
    valid_from: null,
    valid_to: null,
    recorded_at: "2026-09-17T09:00:00Z",
  };
  const memories = [
    {
      ...base,
      memory_id: "pending-1",
      tier: "short",
      status: "pending",
      content: "项目数据库为 SQLite",
    },
    {
      ...base,
      memory_id: "archived-1",
      tier: "long",
      status: "archived",
      content: "历史会议记录",
      predicate: "会议",
      value: "历史会议记录",
    },
  ];
  const commands: any[] = [];
  await page.route("**/api/v1/memories", (route) =>
    route.fulfill({ json: memories }),
  );
  await page.route("**/api/v1/groups", (route) =>
    route.fulfill({
      json: [
        {
          path: ["项目"],
          level: 1,
          scope_type: "project",
          scope_id: "memory-system-demo",
          mode: "extractive_summary",
          summary: "项目数据库为 SQLite",
          memory_refs: ["pending-1:1"],
          summary_items: [
            {
              text: "项目 · 数据库：SQLite",
              memory_ref: "pending-1:1",
              assertion: "stated",
              evidence_count: 1,
            },
          ],
          synthesis: {
            summary: "项目使用 SQLite 数据库。",
            source_refs: ["pending-1:1"],
          },
        },
      ],
    }),
  );
  await page.route("**/api/v1/memories/*/evolve", async (route) => {
    const body = route.request().postDataJSON();
    commands.push(body);
    const memory = memories.find((item) =>
      route.request().url().includes(item.memory_id),
    )!;
    memory.status = "active";
    memory.revision++;
    await route.fulfill({ json: memory });
  });
  await page.route("**/api/v1/maintenance", (route) =>
    route.fulfill({
      json: {
        applied: [],
        review: [{ memory_id: "archived-1", recommendation: "review_archive" }],
        warnings: [],
        group_count: 1,
        retention: [
          {
            memory_id: "archived-1",
            strength: 0.2,
            age_days: 180,
            recommendation: "review_archive",
            evidence_reinforcement: 1,
          },
        ],
      },
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "长期", exact: true }).click();
  await expect(page.locator(".review-queue")).toContainText("待确认候选 · 1");
  await page.getByRole("button", { name: "审阅这条候选", exact: true }).click();
  await expect(page.getByLabel("演化动作", { exact: true })).toHaveValue(
    "activate",
  );
  await page.getByRole("button", { name: "确认激活候选", exact: true }).click();
  expect(commands[0]).toMatchObject({
    action: "activate",
    expected_version: 1,
    expected_revision: 4,
  });
  await expect(page.locator(".review-queue")).toHaveCount(0);
  await page.getByText("非有效长期记忆与历史", { exact: true }).click();
  await page
    .locator(".archived-memories")
    .getByRole("button", { name: "审阅演化" })
    .click();
  await expect(page.getByLabel("演化动作", { exact: true })).toHaveValue(
    "restore",
  );
  await page.getByRole("button", { name: "确认恢复归档记忆" }).click();
  expect(commands[1]).toMatchObject({
    action: "restore",
    expected_revision: 4,
  });
  await page.locator(".summary-groups > summary").click();
  await page.locator(".summary-group > summary").click();
  await expect(page.locator(".synthesis")).toContainText(
    "模型辅助摘要 · 派生视图",
  );
  await page.getByRole("button", { name: "运行记忆维护", exact: true }).click();
  await expect(page.getByLabel("维护结果")).toContainText("1 项建议待审阅");
  await page.getByText("保留强度与归档建议", { exact: true }).click();
  await expect(page.getByLabel("年龄与证据保留强度")).toHaveAttribute(
    "value",
    "0.2",
  );
  await page.screenshot({
    path: "test-results/memory-maintenance-desktop.png",
    fullPage: true,
  });
});

test("检索可读性：中文取舍与真实通道，原始标识折叠保留", async ({ page }) => {
  await memoryBackend(page, { stageStream: true, readableTrace: true });
  await page.goto("/");
  await send(page);
  await expect(page.locator(".message-assistant")).toHaveCount(1);
  await page.getByRole("button", { name: "检索", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "本轮做了哪些取舍" }),
  ).toBeVisible();
  await expect(
    page.getByText("分别寻找多个信息项", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("仍需核对证据是否充分", { exact: true }),
  ).toBeVisible();
  await page.getByText("全文索引已筛选候选", { exact: true }).click();
  await expect(page.getByLabel("运行说明")).toContainText(
    "8 条合格记忆中有 3 条进入词法重排",
  );
  const raw = page.getByLabel("运行说明").locator("pre");
  await expect(raw).not.toBeVisible();
  await page.getByText("原始记录（运行说明）", { exact: true }).click();
  await expect(raw).toContainText("future_diagnostic: custom opaque payload");
  await page.getByText("原始记录（运行说明）", { exact: true }).click();
  await expect(
    page.getByText("本轮未启用向量检索。", { exact: false }),
  ).toHaveCount(1);
  await expect(page.getByText("词法匹配", { exact: true })).toHaveCount(1);
  await expect(page.getByText("关系链支撑证据", { exact: true })).toHaveCount(
    1,
  );
  await expect(page.getByText("其他匹配通道", { exact: true })).toHaveCount(1);
  await page.locator(".trace-scroll").evaluate((element) => {
    element.scrollTop = 270;
  });
  await page.screenshot({
    path: "test-results/memory-readable-trace.png",
    fullPage: true,
  });
});

test("来源随回答持久化：历史不串号、双来源、Markdown安全、Escape返回焦点", async ({ page }) => {
  const state = await memoryBackend(page, { citationScenario: true, stageStream: true });
  await page.goto("/");
  await send(page);
  await expect(page.locator(".message-assistant")).toHaveCount(1);
  await send(page);
  await expect(page.locator(".message-assistant")).toHaveCount(2);
  expect(state.events[1].requestBody.events[0].answer_id).toBe("answer-1");
  expect(state.events[1].requestBody.events[0].answer_context).toBeUndefined();
  await page.reload();
  const first = page.locator(".message-assistant").first();
  const firstCitation = first.getByRole("button", { name: "查看来源 1", exact: true });
  await expect(first.locator("code")).toHaveText("【来源1】");
  await expect(first.getByRole("link", { name: "【来源2】", exact: true })).toHaveAttribute("href", "https://example.com");
  await firstCitation.click();
  await expect(page.getByRole("dialog")).toContainText("第 1 轮的昵称记忆");
  await expect(page.getByRole("dialog")).not.toContainText("第 2 轮的昵称记忆");
  await expect(page.getByRole("dialog").locator("blockquote")).toContainText("我偏好中文说明");
  await page.screenshot({ path: "test-results/premium-source-dialog.png", fullPage: true });
  await page.keyboard.press("Escape");
  await expect(firstCitation).toBeFocused();
  await first.getByRole("button", { name: "查看来源 2", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("第 1 轮的项目记忆");
  await expect(page.getByRole("dialog")).toContainText("请长期记住项目代号是蓝鲸");
  await page.getByRole("button", { name: "关闭来源" }).click();
  await first.getByRole("button", { name: "查看来源 99", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("无法定位此来源");
  await page.keyboard.press("Escape");
  await first.locator(".process-toggle").click();
  await page.getByRole("button", { name: "收起记忆面板", exact: true }).click();
  await page.screenshot({ path: "test-results/premium-conversation.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await firstCitation.click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: "test-results/premium-source-mobile.png", fullPage: true });
});

test("旧回答缺失来源快照时明确告知，不借用最近回答", async ({ page }) => {
  const state = await memoryBackend(page);
  state.events.push({ turn_id: "legacy", events: [{ role: "assistant", content: "旧回答【来源1】", occurred_at: "2026-09-17T09:00:00Z" }] });
  await page.goto("/");
  await send(page);
  await expect(page.locator(".message-assistant")).toHaveCount(2);
  await page.locator(".message-assistant").first().getByRole("button", { name: "查看来源 1" }).click();
  await expect(page.getByRole("dialog")).toContainText("来源记录不可用");
  await expect(page.getByRole("dialog")).not.toContainText("用户偏好中文说明");
});

test("正在处理显示真实灰字阶段，等待时可展开查询图，尊重减少动态效果", async ({ page }) => {
  await memoryBackend(page);
  await page.goto("/");
  await page.emulateMedia({ reducedMotion: "reduce" });
  // Feed the browser parser a real streaming Response and hold completion separately.
  await page.evaluate(() => {
    const original = window.fetch;
    (window as any).finishTestStream = null;
    window.fetch = async (...args) => {
      if (!String(args[0]).endsWith("/answer/stream")) return original(...args);
      const encoder = new TextEncoder();
      return new Response(new ReadableStream({ start(controller) {
        controller.enqueue(encoder.encode(JSON.stringify({ type: "stage", phase: "planning", detail: "正在查找 zfc 的昵称", elapsed_ms: 12, plan: { route: "both", semantic_queries: ["zfc 的昵称"], required_info: ["昵称"], keywords: ["zfc"], temporal_mode: "current", depth: 5, subject: "zfc", predicate: "昵称", as_of: null } }) + "\n"));
        (window as any).finishTestStream = () => controller.close();
      } }), { headers: { "Content-Type": "application/x-ndjson" } });
    };
  });
  await send(page);
  await expect(page.locator(".live-detail")).toHaveText("正在查找 zfc 的昵称");
  await expect(page.locator(".process-toggle")).toContainText("正在理解问题");
  await page.locator(".process-toggle").click();
  await expect(page.locator(".query-intent")).toContainText("zfc 的昵称");
  await expect(page.locator(".process-events li")).toHaveCount(1);
  expect(await page.locator(".process-spark").evaluate((element) => getComputedStyle(element).animationName)).toBe("none");
  await page.screenshot({ path: "test-results/premium-live-process.png", fullPage: true });
  await page.evaluate(() => (window as any).finishTestStream());
  await expect(page.getByRole("alert")).toContainText("未收到完整回答");
  await expect(page.locator(".process-toggle")).toContainText("运行中断");
});

test("检索图明确区分无需检索和检索完成但零证据", async ({ page }) => {
  const state = await memoryBackend(page, { stageStream: true });
  await page.goto("/");
  await send(page);
  await expect(page.locator(".message-assistant")).toHaveCount(1);
  const receipt = state.receipts["answer-1"];
  receipt.sources = [];
  receipt.plan.route = "none";
  receipt.selected_k = 0;
  receipt.query_steps = [];
  await page.reload();
  await page.locator(".process-toggle").click();
  await expect(page.locator(".retrieval-routes")).toContainText("本轮无需检索记忆");
  await expect(page.locator(".route-library")).toHaveCount(0);
  receipt.plan.route = "both";
  receipt.query_steps = [{ phase: "short_retrieval", action: "semantic_lexical_symbolic_recall", order: 1, input_count: 3, output_count: 0, detail: "没有匹配" }];
  await page.reload();
  await page.locator(".process-toggle").click();
  await expect(page.locator(".retrieval-routes")).toContainText("本轮未找到可用的相关记忆");
  await expect(page.locator(".route-library")).toHaveCount(2);
  await expect(page.locator(".route-library").first()).toContainText("3 条输入 · 0 条召回");
});

test("动态K不再由界面固定上限，侧栏支持拖拽键盘和持久收起", async ({ page }) => {
  const state = await memoryBackend(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await expect(page.getByText("动态 K · 自适应", { exact: true })).toBeVisible();
  await expect(page.getByRole("spinbutton", { name: "检索上限 Top K" })).toHaveCount(0);
  await send(page);
  await expect(page.locator(".message-assistant")).toHaveCount(1);
  expect(state.answers[0].top_k).toBeUndefined();
  const handle = page.getByRole("separator", { name: "调整记忆面板宽度" });
  const before = Number(await handle.getAttribute("aria-valuenow"));
  await handle.focus(); await page.keyboard.press("ArrowLeft");
  await expect(handle).toHaveAttribute("aria-valuenow", String(before + 24));
  const box = (await handle.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + 100); await page.mouse.down();
  await page.mouse.move(box.x - 85, box.y + 100); await page.mouse.up();
  const resized = Number(await handle.getAttribute("aria-valuenow"));
  expect(resized).toBeGreaterThan(before + 50);
  await page.reload(); await expect(handle).toHaveAttribute("aria-valuenow", String(resized));
  await page.getByRole("button", { name: "收起记忆面板", exact: true }).click();
  await page.reload(); await expect(page.getByRole("button", { name: "展开记忆面板", exact: true })).toBeVisible();
  await expect(handle).toHaveCount(0);
});

test("会话列表可搜索切换，草稿保留且不携带上一会话运行记录", async ({ page }) => {
  await memoryBackend(page);
  const sessions = [
    { session_id: "session-demo-123456", topic: "原会话", project_id: "project-one", created_at: "2026-09-17T09:00:00Z" },
    { session_id: "session-second", topic: "产品评审", project_id: "project-two", created_at: "2026-09-17T10:00:00Z" },
  ];
  await page.route("**/api/v1/sessions", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return route.fulfill({ json: sessions });
  });
  await page.route("**/api/v1/sessions/session-second", (route) => route.fulfill({ json: { session: { ...sessions[1], goal: "", plan: [], pending_tasks: [], summary: "", processed_sequence: 0, revision: 1 }, recent_turns: [{ turn_id: "old-second", events: [{ role: "user", content: "第二个会话的历史记录", occurred_at: "2026-09-17T10:00:00Z" }] }], short_memories: [], pending_turns: 1 } }));
  await page.goto("/");
  await page.getByLabel("输入消息", { exact: true }).fill("尚未发送的草稿");
  await page.getByRole("button", { name: "切换会话", exact: true }).click();
  await page.getByRole("textbox", { name: "搜索会话" }).fill("project-two");
  await expect(page.locator(".session-option")).toHaveCount(1);
  await page.locator(".session-option").click();
  await expect(page.getByRole("heading", { name: "产品评审", exact: true })).toBeVisible();
  await expect(page.locator(".message-feed")).toContainText("第二个会话的历史记录");
  await expect(page.getByLabel("输入消息", { exact: true })).toHaveValue("");
  await expect(page.locator(".process-toggle")).toHaveCount(0);
  await page.getByRole("button", { name: "切换会话", exact: true }).click();
  await page.locator(".session-option").filter({ hasText: "原会话" }).click();
  await expect(page.getByLabel("输入消息", { exact: true })).toHaveValue("尚未发送的草稿");
  await expect(page.locator(".message-feed")).not.toContainText("第二个会话的历史记录");
});

test("三路检索显示真实通道状态、分库数量与动态预算", async ({ page }) => {
  const state = await memoryBackend(page, { stageStream: true });
  await page.goto("/"); await send(page);
  await expect(page.locator(".message-assistant")).toHaveCount(1);
  const receipt = state.receipts["answer-1"];
  receipt.channels = ["short", "long"].flatMap((tier) => [
    { view: "semantic", tier, status: "disabled", input_count: 0, matched_count: 0, selected_count: 0, detail: "not_configured" },
    { view: "lexical", tier, status: "complete", input_count: 8, matched_count: 3, selected_count: 1, detail: "fts5_bm25" },
    { view: "symbolic", tier, status: "skipped", input_count: 0, matched_count: 0, selected_count: 0, detail: "missing_subject_predicate" },
  ]);
  receipt.dynamic_k = { planned_depth: 8, required_info_count: 4, candidate_limit: 48, safety_cap: 20, target_k: 8, selected_k: 1, token_limit: 2000, used_tokens: 80, selection_policy: "coverage", score_semantics: "relevance" };
  await page.reload(); await page.locator(".process-toggle").click();
  await expect(page.locator(".route-channels .route-channel")).toHaveCount(1);
  await expect(page.locator(".routes-idle")).not.toHaveAttribute("open", "");
  await expect(page.locator(".route-idle-channel.semantic")).not.toBeVisible();
  await expect(page.locator('.route-bank-wire[data-view="semantic"].traversed')).toHaveCount(0);
  await expect(page.locator('.route-bank-wire[data-view="lexical"].traversed')).toHaveCount(2);
  await expect(page.locator(".route-library").last()).toContainText("已检索 · 无入选来源");
  await page.screenshot({ path: "test-results/routes-active-desktop.png", fullPage: true });
  await page.locator(".routes-idle > summary").click();
  await expect(page.locator(".route-idle-channel.semantic")).toContainText("可选 · 未启用");
  await expect(page.locator(".route-idle-channel.semantic")).toContainText("未配置向量服务");
  await expect(page.locator(".route-idle-channel.symbolic")).toContainText("未形成完整的主体与属性条件");
  await page.locator(".route-idle-channel.symbolic").click();
  await expect(page.locator(".channel-outcome")).toContainText("未触发精确匹配");
  await expect(page.locator(".channel-history-note")).toHaveCount(0);
  await page.locator(".routes-idle > summary").click();
  await expect(page.locator(".route-detail")).toHaveCount(0);
  await page.locator(".route-channel.lexical").click();
  await expect(page.locator(".route-detail")).toContainText("8 条检查 / 3 条命中 / 1 条最终入选");
  await expect(page.locator(".route-budget")).toContainText("规划深度 8");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await expect(page.locator(".route-channel.lexical")).toBeVisible();
  await expect(page.locator(".routes-mobile-ledger > button").first()).toContainText("1 条入选来源");
  await page.locator(".routes-idle > summary").click();
  await expect(page.locator(".route-idle-channel.semantic")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: "test-results/routes-mobile.png", fullPage: true });
});

test("精确字段查询将语义检索显示为按需跳过而非未配置", async ({ page }) => {
  const state = await memoryBackend(page, { stageStream: true });
  await page.goto("/"); await send(page);
  await expect(page.locator(".message-assistant")).toHaveCount(1);
  const receipt = state.receipts["answer-1"];
  receipt.retrieval_mode = "hybrid";
  receipt.channels = ["short", "long"].flatMap((tier) => [
    { view: "semantic", tier, status: "skipped", input_count: 0, matched_count: 0, selected_count: 0, detail: "exact_field_lookup", elapsed_ms: 0 },
    { view: "lexical", tier, status: "complete", input_count: 1, matched_count: 1, selected_count: 1, detail: "fts5_candidates_bm25_scored", elapsed_ms: 0.5 },
    { view: "symbolic", tier, status: "complete", input_count: 1, matched_count: 1, selected_count: 1, detail: "exact_subject_predicate", elapsed_ms: 0.1 },
  ]);
  await page.reload(); await page.locator(".process-toggle").click();
  await expect(page.locator(".route-channels .route-channel")).toHaveCount(2);
  await page.locator(".routes-idle > summary").click();
  await expect(page.locator(".route-idle-channel.semantic")).toContainText("本次未触发");
  await expect(page.locator(".route-idle-channel.semantic")).toContainText("省去向量检索");
  await expect(page.locator(".route-idle-channel.semantic")).not.toContainText("未配置向量服务");
});

test("检索状态区分实际零命中、按需跳过与历史统计缺失", async ({ page }) => {
  const state = await memoryBackend(page, { stageStream: true });
  await page.goto("/"); await send(page);
  await expect(page.locator(".message-assistant")).toHaveCount(1);
  const receipt = state.receipts["answer-1"];
  receipt.retrieval_mode = "lexical_baseline";
  receipt.channels = ["semantic", "lexical", "symbolic"].flatMap((view) => [
    { view, tier: "short", status: "complete", input_count: 3, matched_count: 0, selected_count: 0, detail: "checked" },
    { view, tier: "long", status: "skipped", input_count: 0, matched_count: 0, selected_count: 0, detail: "short_evidence_sufficient" },
  ]);
  await page.reload(); await page.locator(".process-toggle").click();
  await expect(page.locator(".route-channel")).toHaveCount(3);
  await expect(page.locator(".route-channel.semantic")).toContainText("0 次命中");
  await expect(page.locator(".routes-idle")).toHaveCount(0);
  await page.screenshot({ path: "test-results/routes-three-desktop.png", fullPage: true });
  await page.locator(".route-channel.semantic").click();
  await expect(page.locator(".route-detail")).toContainText("无需补查长期库");
  await page.getByRole("button", { name: "长期库", exact: true }).click();
  await expect(page.locator(".channel-outcome")).toContainText("短期记忆已满足");
  receipt.sources = [];
  receipt.channels = receipt.channels.map((channel: any) => ({ ...channel, status: "skipped", detail: "no_eligible_memories" }));
  await page.reload(); await page.locator(".process-toggle").click();
  await expect(page.locator(".routes-canvas")).toHaveCount(0);
  await expect(page.locator(".retrieval-routes")).toContainText("本次没有执行检索通道匹配");
  await page.locator(".routes-idle > summary").click();
  await expect(page.locator(".route-idle-channel.symbolic")).toContainText("没有可检查的记忆");
  receipt.channels = [];
  receipt.retrieval_mode = "hybrid";
  await page.reload(); await page.locator(".process-toggle").click();
  await expect(page.locator(".route-channel")).toHaveCount(3);
  await expect(page.locator(".route-channel.symbolic")).toContainText("未记录通道统计");
});

test("长期对象整合卡保留不同属性和各自原文", async ({ page }) => {
  await memoryBackend(page);
  const fact = (id: string, predicate: string, value: string, quote: string) => ({ memory_id: id, version: 1, revision: 1, session_id: "session-demo-123456", tier: "long", status: "active", kind: "fact", content: quote, subject: "zfc", predicate, value, keywords: [], evidence: [{ turn_id: "turn-"+id, event_index: 0, quote }], assertion: "stated", durable: true, scope_type: "user", scope_id: "local-user", hierarchy_path: [], valid_from: null, valid_to: null, recorded_at: "2026-09-17T09:00:00Z" });
  await page.route("**/api/v1/entities", (route) => route.fulfill({ json: [{ entity_id: "zfc-user", subject: "zfc", scope_type: "user", scope_id: "local-user", summary: "zfc：别名是飞猪；特性是狂暴。", facts: [fact("alias", "别名", "飞猪", "zfc就是飞猪"), fact("trait", "特性", "狂暴", "记住zfc非常狂暴")], pending_facts: [], conflict_predicates: [], updated_at: "2026-09-17T09:00:00Z" }] }));
  await page.goto("/"); await page.getByRole("button", { name: "长期", exact: true }).click();
  await expect(page.locator(".entity-memory")).toHaveCount(1);
  await expect(page.locator(".entity-summary")).toContainText("别名是飞猪；特性是狂暴");
  await page.getByText("2 条属性 · 查看来源与演化", { exact: true }).click();
  await expect(page.locator(".entity-fact")).toHaveCount(2);
  await page.locator(".entity-fact").first().getByText("1 条原始证据").click();
  await expect(page.locator(".entity-fact").first().locator("blockquote")).toHaveText("zfc就是飞猪");
  await page.locator(".entity-fact").last().getByText("1 条原始证据").click();
  await expect(page.locator(".entity-fact").last().locator("blockquote")).toHaveText("记住zfc非常狂暴");
  await expect(page.locator(".entity-originals")).not.toHaveAttribute("open", "");
});

test("检索通道展示实际条件和未命中内容，保存上限明确", async ({ page }) => {
  const state = await memoryBackend(page, { stageStream: true });
  await page.goto("/"); await send(page); await expect(page.locator(".message-assistant")).toHaveCount(1);
  const receipt = state.receipts["answer-1"];
  receipt.channels = [{ view: "symbolic", tier: "short", status: "complete", input_count: 25, matched_count: 0, selected_count: 0, detail: "exact_subject_predicate", query_conditions: { subject: "zfc", predicate: "性格", lexical_terms: ["zfc", "狂暴"] }, candidates: [{ memory_id: "m1", version: 1, revision: 1, subject: "zfc", predicate: "特性", value: "狂暴", content: "zfc非常狂暴", matched: false, selected: false, score: null, reason: "属性不匹配：性格与特性不同" }], candidate_total: 25, candidate_limit: 20, candidates_truncated: true }];
  await page.reload(); await page.locator(".process-toggle").click(); await page.locator(".route-channel.symbolic").click();
  await expect(page.locator(".channel-conditions")).toContainText("匹配属性");
  await expect(page.locator(".channel-conditions")).toContainText("性格");
  await expect(page.locator(".candidate-fact")).toHaveText("zfc非常狂暴");
  await expect(page.locator(".candidate-reason")).toContainText("属性不匹配");
  await expect(page.locator(".channel-truncated")).toContainText("1 / 25");
  await page.locator(".candidate-filters").getByRole("button", { name: "最终入选", exact: true }).click();
  await expect(page.locator(".channel-empty")).toContainText("没有符合");
  await page.locator(".candidate-filters").getByRole("button", { name: "未命中", exact: true }).click();
  await expect(page.locator(".candidate-fact")).toHaveCount(1);
});
