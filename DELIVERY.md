# 长短期记忆系统交付

开发分支为 `full-forward`，当前后端版本为 `0.3.0`。保留 SimpleMem 式意图规划、短期优先、多视图召回与动态 K 主线；增加自动整理与演化、可恢复维护任务、FTS5/向量内容投影、证据约束的别名关联、分层摘要及动态运行展示。本轮排除 Engine 集成。

## 启动

在 `SimpleMem` 或交付包解压根目录下分别打开两个 PowerShell 终端。环境遵循 [AGENTS.md](AGENTS.md)。完整仓库可用 `conda env create -f environment.yml` 重建原环境；精简交付包未包含上游项目环境，首次运行可用 `conda create -n simplemem-agentmemory python=3.12 pip` 创建环境，再执行下面的后端安装命令。已有同名环境时直接复用。

后端：

```powershell
cd memory-system
conda run --no-capture-output -n simplemem-agentmemory python -m pip install -e ".[dev]"
conda run --no-capture-output -n simplemem-agentmemory python -m agent_memory
```

前端：

```powershell
cd memory-ui
conda run --no-capture-output -n simplemem-agentmemory pnpm install --frozen-lockfile
conda run --no-capture-output -n simplemem-agentmemory pnpm dev
```

打开 `http://127.0.0.1:5173`。开发前端通过 Vite 同源代理访问 `127.0.0.1:8088`，后端 API 文档为 `http://127.0.0.1:8088/docs`。模型设置只放后端私有 `.env`，模板见 [配置示例](memory-system/.env.example)。生产配置、反向代理和身份边界见 [运维说明](memory-system/OPERATIONS.md)。

## 可演示流程

1. 建立项目会话，记录带事实的消息，查看待整理数量和原始对话。
2. 回答前自动整理，或点击整理单独核对结构化记忆及逐字证据。明确持久的用户/项目事实可自动晋升；问题与助手回复不作为独立肯定事实。
3. 创建同一项目的新会话，查询长期事实，查看检索路由、多视图、筛选和动态 K。
4. 输入明确更正或现实变更，观察自动纠错/版本替代；歧义进入待确认队列。维护页提供分层摘要、归档建议和恢复操作，源记录保持可追溯。
5. 在运行观测页对同一上下文执行基线、冷缓存、热缓存，比较真实耗时与避免的生成调用。修改对话或记忆后再次验证失效。

界面展示来自后端；模型失败会明确显示错误，不生成演示答案。浏览器测试使用独立夹具和临时数据库，与生产流程分开。

运行回路与时间线由 NDJSON 服务事件驱动，记录实际抽取、规划、召回、生成与复验阶段；“回放”明确使用已有记录，不模拟模型私有思维链。页面内置三组逐步实验引导，示例只填入输入框，发送后才写入。

`MEMORY_MAINTENANCE_INTERVAL=300` 默认每五分钟为本地配置的主体执行维护；设为 `0` 禁用。模型摘要仅为带来源的派生导航视图，不充当新事实。独立运行进程启动该 worker，SDK 宿主需自行调度。

## 验证入口

最终执行汇总见 [交付验证记录](memory-system/docs/VERIFICATION.md)。

```powershell
cd memory-system
conda run --no-capture-output -n simplemem-agentmemory python -m pytest -q -p no:cacheprovider
conda run --no-capture-output -n simplemem-agentmemory python -m ruff check src tests scripts
conda run --no-capture-output -n simplemem-agentmemory python -m ruff format --check src tests scripts
conda run --no-capture-output -n simplemem-agentmemory python scripts/evaluate.py
conda run --no-capture-output -n simplemem-agentmemory python scripts/http_load.py
# 会实际调用配置的模型：
conda run --no-capture-output -n simplemem-agentmemory python scripts/live_smoke.py
conda run --no-capture-output -n simplemem-agentmemory python scripts/practical_quality.py
cd ../memory-ui
conda run --no-capture-output -n simplemem-agentmemory pnpm build
conda run --no-capture-output -n simplemem-agentmemory pnpm exec playwright install chromium
conda run --no-capture-output -n simplemem-agentmemory pnpm test
conda run --no-capture-output -n simplemem-agentmemory pnpm test:integration
```

模型小样本已经验证真实抽取、跨会话召回和缓存命中；统计性问答质量仍需独立数据集。完整证据见 [检索与加速评测](memory-system/docs/EVALUATION.md)、[真实模型记录](memory-system/docs/LIVE_SMOKE.md)、[HTTP负载](memory-system/docs/HTTP_LOAD.md)。

本轮 [14个实用会话场景](memory-system/docs/PRACTICAL_QUALITY.md)覆盖未知关系、zfc告知与纠错、跨会话、别名关联、临时覆盖、项目隔离及长会话。严格词面规则13/14；剩余一题当前值和版本正确，但回答附带旧值，仍保留失败。初轮12/14结果另存，不将人工复核改写成自动满分。该报告不同于旧十题集，不能合并分母或宣称公开基准准确率。

[索引对照实验](memory-system/docs/INDEX_BENCHMARK.md)记录1万条候选的首次建索引、暖查询和向量复用。权威库仍读取授权快照，向量仍精确扫描；没有实现ANN或分布式扩展。真实Embedding服务未配置，线上当前使用FTS/BM25、符号、层级和实体关联；向量适配及持久缓存使用协议测试验证。

[真实模型10题小集](memory-system/docs/LIVE_QUALITY.md)保留了[修复前7/10的记录](memory-system/docs/LIVE_QUALITY_INITIAL.md)，修复后严格词面规则为9/10。剩余一题回答了正确的新截止日，并提到已被替代的旧日期，被“禁止出现旧日期”规则判错；实际来源是新版本。报告保留自动失败并单列人工复核，不通过重复运行筛选满分。复现命令为 `python scripts/live_quality.py`，该保守规则未全部通过时退出码为1，须读逐题证据。

## 课程与部署交接

可分发包为 `memory-system/dist/full-forward-delivery.zip`，包含源代码、测试、文档、后端 wheel、前端 `dist/` 与逐文件 SHA-256 清单；打包采用明确白名单，不包含私有 `.env`、运行数据库或 `node_modules`。重建命令：在 `memory-system` 执行 `python scripts/package_delivery.py`。`requirements.lock` 记录本次 Windows/Python 3.12 验证的17个运行时依赖版本，不是所有平台兼容性承诺。

- [课程需求与来源页码](memory-system/docs/COURSE_REQUIREMENTS.md)
- [超过8000字的技术报告](memory-system/docs/技术报告.md)
- [20分钟讲解与答辩演示脚本](memory-system/docs/答辩演示脚本.md)
- [宿主SDK接入与验证命令](memory-system/docs/SDK_INTEGRATION.md)
- [验收矩阵](memory-system/docs/ACCEPTANCE.md)

当前交付为经过本地验证的单节点候选版本，不宣称任意规模或任意宿主已商用验收。完整课程宿主未包含在工作区，正式 `validate_plugin` 和宿主 HTTP/SSE 验收需在目标环境执行；课程最终视频/PPT可按演示脚本录制制作。多租户身份、目标容量、供应商质量和备份保留政策也应由部署方完成验收。
