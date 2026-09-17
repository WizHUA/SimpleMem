import { expect, test } from "@playwright/test";
const token = "integration-test-only-token-0123456789";

test("真实后端：同源代理、Bearer、幂等写入、版本替代、审计撤回和模型未配置错误", async ({
  page,
  request,
}) => {
  const response = await request.get("http://127.0.0.1:8095/api/v1/memories", {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(response.ok()).toBeTruthy();
  const seeds = await response.json();
  const sessionId = seeds[0].session_id;
  await page.addInitScript(
    (id) => localStorage.setItem("memory-session-id", id),
    sessionId,
  );
  await page.goto("/");
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("button", { name: "连接设置", exact: true }).click();
  await page.getByLabel("服务访问令牌", { exact: true }).fill(token);
  await page.getByRole("button", { name: "保存并重新连接" }).click();
  await expect(
    page.getByRole("heading", { name: "真实 HTTP 集成验收" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => sessionStorage.getItem("memory-service-token")),
  ).toBe(token);
  expect(
    await page.evaluate(() => localStorage.getItem("memory-service-token")),
  ).toBeNull();
  await page.getByRole("button", { name: "审阅演化", exact: true }).click();
  await expect(page.getByLabel("演化动作", { exact: true })).toHaveValue(
    "supersede",
  );
  await expect(page.getByRole("dialog")).toContainText(
    "旧版本：用户偏好中文说明",
  );
  await page
    .getByRole("button", { name: "确认替代旧版本", exact: true })
    .click();
  await expect(page.locator(".episode")).toContainText("用户偏好英文说明");
  await expect(page.locator(".episode")).toContainText("v2");
  await page
    .locator(".episode")
    .getByRole("button", { name: "查看演化历史" })
    .click();
  await expect(page.getByRole("dialog")).toContainText("关闭旧版本有效期");
  await expect(page.getByRole("dialog")).toContainText("替代旧版本");
  await page.getByRole("button", { name: "关闭窗口", exact: true }).click();
  await page
    .locator(".episode")
    .getByRole("button", { name: "审阅 / 撤回" })
    .click();
  await page.getByRole("button", { name: "确认撤回记忆", exact: true }).click();
  await expect(page.locator(".episode")).toHaveCount(0);
  await page.getByText("非有效长期记忆与历史", { exact: true }).click();
  await expect(page.locator(".archived-memories")).toContainText("已撤销");
  await page.getByLabel("输入消息", { exact: true }).fill("我希望继续测试界面");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.locator(".message-user")).toHaveCount(3);
  await page.getByRole("button", { name: "重试已保存的问题" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.locator(".message-user")).toHaveCount(3);
  // Exercise the actual browser API helper twice; the real backend hashes the full body.
  const result = await page.evaluate(async (id) => {
    const modulePath = "/src/api.ts";
    const { api } = await import(modulePath);
    await api.append(id, "user", "相同幂等请求只记一次", "ui-real-idempotency");
    await api.append(id, "user", "相同幂等请求只记一次", "ui-real-idempotency");
    return (await api.session(id)).recent_turns.filter(
      (turn: { request_id: string }) =>
        turn.request_id === "ui-real-idempotency",
    ).length;
  }, sessionId);
  expect(result).toBe(1);
  await page.getByRole("button", { name: "短期", exact: true }).click();
  await page.screenshot({
    path: "integration-results/real-backend-smoke.png",
    fullPage: true,
  });
});
