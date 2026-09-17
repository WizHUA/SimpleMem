# 交付验证记录

2026-09-17，`full-forward`，后端版本 `0.2.0`。测试使用仓库指定 `simplemem-agentmemory` 环境；运行库版本见 [requirements.lock](../requirements.lock)。本记录说明已执行范围，不替代目标环境验收。

| 检查 | 实际结果 | 范围 |
|---|---|---|
| Python 测试 | 121 passed | 存储、提取、检索、推理复用、并发取消、隔离、HTTP与SDK契约 |
| Ruff 检查与格式 | 通过 | `src tests scripts` |
| 前端 TypeScript 与 Vite 构建 | 通过 | 生成可部署静态产物 |
| Playwright 交互 | 6 passed | 测试夹具，含错误恢复、幂等、加速、窄屏、中文输入法 |
| Playwright真实后端集成 | 1 passed | 真实FastAPI/临时SQLite，Bearer、版本替代、审计、撤回、幂等 |
| 真实模型冒烟 | 通过 | 抽取两条长期记忆、跨会话召回、生成缓存命中，见 [LIVE_SMOKE](LIVE_SMOKE.md) |
| 真实模型质量小集 | 严格规则9/10，0请求错误 | 初始7/10已归档；剩余误判与非盲复核见 [LIVE_QUALITY](LIVE_QUALITY.md) |
| 合成检索与加速 | 已执行并保留不足 | 词法改写挑战不通过；生成调用合并有效，见 [EVALUATION](EVALUATION.md) |
| ASGI 30/100并发 | 失败率0%，重启记录390/390 | 进程内负载，不是网络SLA，见 [HTTP_LOAD](HTTP_LOAD.md) |
| 前端依赖审计 | 未发现已知漏洞 | 实际执行 `pnpm audit --audit-level high` |
| 后端依赖审计 | 未发现已知漏洞 | pip-audit 2.10.1 扫描锁定的17个运行时包；不代表无未知漏洞 |
| 后端wheel构建/独立导入 | 通过 | 可安装分发包；核心无课程宿主依赖 |
| 真实课程宿主 | 未执行 | 现有工作区和参考包缺完整宿主；已有 [真实验证脚本](../scripts/validate_host_sdk.py) |

Python测试有一条来自 Starlette 测试客户端的 AnyIO 弃用提示；前端终端有颜色环境变量提示，均未影响通过结果。

真实模型结果来自真实调用与合成测试数据，不是公开基准或用户长期对话数据集。十题中第9题正确给出新日期，但说明旧日期后被保守词面规则判错；报告保留失败，不将人工解释改写成自动满分。

## 交付包

`dist/full-forward-delivery.zip` 包含白名单源码、测试、文档、前端构建、当前后端wheel及 `MANIFEST.sha256.json`。不包含私有 `.env`、运行数据库、模型密钥或依赖目录。通过 `scripts/package_delivery.py` 重建。

部署前仍需课程宿主、实际身份系统、HTTPS、目标容量、真实 embedding 服务、数据保留策略和恢复演练验收。当前实现是受控单节点交付版本，未声称已验证多副本水平扩展。
