# 长短期记忆系统交付

开发分支为 `full-forward`。保留 SimpleMem 式意图规划、短期优先、多视图召回与动态 K 主线；长期层保留有效期、版本历史和来源，H-MEM 当前用于组织展示。

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
2. 点击整理，核对结构化短期记忆及逐字证据。明确“存入长期记忆”的稳定用户/项目事实可直接固化。
3. 创建同一项目的新会话，查询长期事实，查看检索路由、多视图、筛选和动态 K。
4. 输入纠正事实后整理，选择已有长期记录进行版本替代；查看历史，再显式撤回，验证旧事实退出当前检索。
5. 在运行观测页对同一上下文执行基线、冷缓存、热缓存，比较真实耗时与避免的生成调用。修改对话或记忆后再次验证失效。

界面展示来自后端；模型失败会明确显示错误，不生成演示答案。浏览器测试使用独立夹具和临时数据库，与生产流程分开。

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
cd ../memory-ui
conda run --no-capture-output -n simplemem-agentmemory pnpm build
conda run --no-capture-output -n simplemem-agentmemory pnpm exec playwright install chromium
conda run --no-capture-output -n simplemem-agentmemory pnpm test
conda run --no-capture-output -n simplemem-agentmemory pnpm test:integration
```

模型小样本已经验证真实抽取、跨会话召回和缓存命中；统计性问答质量仍需独立数据集。完整证据见 [检索与加速评测](memory-system/docs/EVALUATION.md)、[真实模型记录](memory-system/docs/LIVE_SMOKE.md)、[HTTP负载](memory-system/docs/HTTP_LOAD.md)。

[真实模型10题小集](memory-system/docs/LIVE_QUALITY.md)保留了[修复前7/10的记录](memory-system/docs/LIVE_QUALITY_INITIAL.md)，修复后严格词面规则为9/10。剩余一题回答了正确的新截止日，并提到已被替代的旧日期，被“禁止出现旧日期”规则判错；实际来源是新版本。报告保留自动失败并单列人工复核，不通过重复运行筛选满分。复现命令为 `python scripts/live_quality.py`，该保守规则未全部通过时退出码为1，须读逐题证据。

## 课程与部署交接

可分发包为 `memory-system/dist/full-forward-delivery.zip`，包含源代码、测试、文档、后端 wheel、前端 `dist/` 与逐文件 SHA-256 清单；打包采用明确白名单，不包含私有 `.env`、运行数据库或 `node_modules`。重建命令：在 `memory-system` 执行 `python scripts/package_delivery.py`。`requirements.lock` 记录本次 Windows/Python 3.12 验证的17个运行时依赖版本，不是所有平台兼容性承诺。

- [课程需求与来源页码](memory-system/docs/COURSE_REQUIREMENTS.md)
- [超过8000字的技术报告](memory-system/docs/技术报告.md)
- [20分钟讲解与答辩演示脚本](memory-system/docs/答辩演示脚本.md)
- [宿主SDK接入与验证命令](memory-system/docs/SDK_INTEGRATION.md)
- [验收矩阵](memory-system/docs/ACCEPTANCE.md)

当前交付为经过本地验证的单节点候选版本，不宣称任意规模或任意宿主已商用验收。完整课程宿主未包含在工作区，正式 `validate_plugin` 和宿主 HTTP/SSE 验收需在目标环境执行；课程最终视频/PPT可按演示脚本录制制作。多租户身份、目标容量、供应商质量和备份保留政策也应由部署方完成验收。
