# Memory UI

长短期记忆后端的独立验证界面。界面不保存或构造记忆数据，只在 `localStorage` 保存当前 `session_id`；对话、短期记忆、H-MEM 树和查询 trace 均来自后端 `/api/v1`。

## 运行

先启动 `memory-system` 后端，再运行：

```powershell
cd "C:\Users\zhangjinhan\Desktop\Agent Mem\memory-ui"
pnpm install
pnpm dev
```

打开 `http://127.0.0.1:5173`。生产构建：

```powershell
pnpm build
```

后端地址默认为 `http://127.0.0.1:8088`。部署到其他地址时复制 `.env.example` 为 `.env.local` 并修改 `VITE_API_BASE_URL`。这里不能保存 GLM API Key；模型密钥只能存在后端。

## 对话调用顺序

```text
POST turn(user)
POST answer
POST turn(assistant)
GET session + memories + hierarchy
```

如果 answer 失败，用户消息仍已持久化，界面显示后端错误。达到抽取条件时，短期标签显示待整理数量；点击“整理”才调用 `/extract`，避免调试界面在后台不受控地消耗模型额度。

短期记忆只有在后端返回 `durable=true` 且作用域为 user/project 时显示“晋升长期”。晋升调用版本检查接口，成功后刷新 H-MEM 四层树。

