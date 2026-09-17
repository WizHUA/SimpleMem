# SimpleMem 记忆工作台

配套 `memory-system` 的中文展示前端。主线是**真实对话 → 短期整理 → 长期演化 → 按需检索 → 推理加速观测**。界面不构造记忆，不在浏览器保存模型密钥；当前会话 ID 写入 `localStorage`；可选服务访问令牌仅写入当前标签页的 `sessionStorage`。

## 运行与构建

在 `SimpleMem/memory-ui` 目录运行，沿用仓库指定的 Conda 环境：

```powershell
conda run --no-capture-output -n simplemem-agentmemory pnpm install --frozen-lockfile
conda run --no-capture-output -n simplemem-agentmemory pnpm dev
```

先启动 `memory-system` 后端，再打开 `http://127.0.0.1:5173`。默认请求同源 `/api` 和 `/health`，Vite 开发/预览代理转发到 `http://127.0.0.1:8088`，无需跨域配置。开发代理目标可通过进程环境 `MEMORY_UI_PROXY_TARGET` 修改。生产静态部署应将 `/api` 和 `/health` 反向代理到后端；若改用跨域地址，可通过 `.env.local` 的 `VITE_API_BASE_URL` 配置并重新构建，此时后端 `MEMORY_ALLOWED_ORIGINS` 必须允许网页实际来源。这里不能填写 DeepSeek、GLM 或其他模型 API Key，模型密钥只属于后端。

```powershell
conda run --no-capture-output -n simplemem-agentmemory pnpm build
```

构建输出为 `dist/`，可交给静态托管服务。后端启用 `MEMORY_API_KEY` 时，点击页眉“连接设置”，填写服务访问令牌；它与模型供应商密钥不同，不应放入 `VITE_*` 构建变量。Windows 使用 `--no-capture-output` 避免 Conda 对 UTF-8 构建日志做 GBK 转码。

## 展示流程

1. 发送明确的项目事实或个人偏好。示例按钮仅填入草稿，需主动发送才写入后端。
2. 在“短期”页点击“整理”，查看摘要、结构化记忆和逐字原文证据。抽取为显式操作，防止在演示后台不受控调用模型。
3. 对后端认定 `durable=true` 且作用域为 user/project 的短期记忆点击“晋升长期”。“长期”页按领域、类别、线索和版本呈现有效状态及来源会话。若已有同作用域、主语和关系的长期记忆，“审阅演化”可选择目标版本并指定新事实生效时间；完全相同的事实支持合并证据。暂缓与撤回均先展示影响并要求显式确认。历史按钮读取服务端审计记录，撤回后记忆退出正常检索。暂缓状态当前不支持原地恢复，界面会明确提示。
4. 新建相同项目的会话，询问已记住的信息。在“检索”页核对路由、动态 K、候选、实际步骤、来源和引用。会话级记忆隔离，用户级长期记忆跨项目可见。
5. “观测”页点击“运行无缓存基线”，再运行两次“运行加速查询”。实验只请求 `/answer`，不追加对话，适合比较相同上下文。首次未命中正常；模型、作用域、会话、查询或上下文变化后不会复用原答案。

观测页只显示后端返回的 `acceleration`。缺少字段时明确显示“未返回指标”，不补造数据。Token 数是保守字符估算，非供应商计费值。单次基线与本次请求只是演示对照，不能用来宣称统计意义上的加速倍数。缓存原始生成耗时单独标明，不冒充本次节省耗时。完整检索与权限检查在加速查询中仍会执行。

## 可靠性与交互

对话请求顺序：`POST turn(user) → POST answer → POST turn(assistant) → GET snapshot`。回答失败后可重试已保存的问题，不重写用户事件；助手写入响应中断时复用已生成答案和相同幂等键。事件时间由服务器生成，保持重试请求体完全一致。此恢复状态保存在当前页面内存，刷新页面后需重新检查会话。

新建会话和刷新统一经过操作锁，异步错误显示在全局提示区。支持中文输入法确认键、Shift+Enter 换行、可见键盘焦点、原生对话框焦点管理、减少动画偏好以及 390px 窄屏布局。

## 自动验收

```powershell
conda run --no-capture-output -n simplemem-agentmemory pnpm exec playwright install chromium
conda run --no-capture-output -n simplemem-agentmemory pnpm test
```

Playwright 自动启动 `127.0.0.1:5175`，拦截后端请求作为明确测试夹具。覆盖证据与晋升流程、失败重试、幂等恢复、加速实验不污染会话、离线恢复、窄屏和中文输入法。浏览器截图在被 Git 忽略的 `test-results/` 下，仅是界面验收材料，**不构成真实模型性能证据**。真实后端质量与性能验收应结合 `memory-system` 的测试、基准和实际模型 smoke 结果。

真实后端整合验收（不调用模型、不接触用户数据库）：

```powershell
conda run --no-capture-output -n simplemem-agentmemory pnpm test:integration
```

该命令启动临时 SQLite、真实 FastAPI（8095）和同源 Vite（5176），用明确标记的原文与候选夹具验证 Bearer 接入、真实版本替代、审计历史、撤回、模型未配置错误和真正的幂等请求哈希。测试进程退出后回收临时库。它验证传输与持久化的集成，不证明抽取模型质量。
