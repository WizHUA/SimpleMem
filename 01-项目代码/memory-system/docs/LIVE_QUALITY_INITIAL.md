# 真实模型合成小集检查

执行时间：2026-09-17T03:34:45.683519+00:00。
提供商：deepseek；模型：deepseek-flash；embedding配置：False。

真实模型调用、临时 SQLite、合成四事实。全程关闭回答缓存，问题顺序固定，每题只执行一次。
本实验采用词面规则，不是公开语义质量基准，也没有使用模型评委。
引用仅检查存在合法编号，不等同引用忠实度。拒猜仅检查否定/缺少信息措辞，不足以证明答案没有幻觉。

计划10题，实际尝试10题，完成10题，请求错误0题；
词面与来源规则通过7/10题（未执行题不计入该分母，仍单列）。

## 数据与维护流程

请把以下四条已确认事实作为项目级长期记忆保存，仅适用于星桥项目，不是全局用户偏好：星桥项目报告的默认输出语言是中文；星桥项目报告截止日期是2026年11月20日；星桥项目默认数据库是PostgreSQL；星桥项目报告导出格式是PDF。

四条事实一次真实抽取。若模型返回可持久化的短期候选，由脚本显式提升，并记录ID。
更正使用第二次真实抽取；仅在主体/谓词精确匹配且候选唯一时，脚本显式执行 supersede。
没有自动修补抽取字段或更改规则来追求通过率。维护动作不是模型自动关系判断。

阶段 `initial_extraction`：`processed`。

阶段 `correction`：`failed`。

维护未执行原因：Expected one exact subject/predicate match；匹配数 0。

更正候选主体 `星桥项目报告截止日期`，谓词 `截止日期`，值 `2026-12-05`，生效时间 `2026-12-05T00:00:00Z`。

更正查询是整条维护流程的检查。如果精确关系匹配失败，旧记忆仍有效，后续失败不能归因于已执行的 supersede。
更正规则禁止回答出现旧日期，属于保守词面约束；正确的历史对比也可能被它判错，需查看原始回答。

## 逐题原始结果

### 1. 语言直接查询

问题：星桥项目报告默认使用什么语言？

状态：completed；规则通过：True。

星桥项目报告的默认输出语言是中文。【来源1】

规则检查：`{"expected_terms": true, "forbidden_terms_absent": true, "retrieved_source_present": true, "valid_citation_present": true}`

来源：`["星桥项目报告的默认输出语言是中文。", "星桥项目报告截止日期是2026年11月20日。", "星桥项目报告导出格式是PDF。", "星桥项目默认数据库是PostgreSQL。"]`

### 2. 日期回忆

问题：星桥项目报告的截止日期是哪一天？

状态：completed；规则通过：True。

星桥项目报告的截止日期是 **2026年11月20日**。【来源1】

规则检查：`{"expected_terms": true, "forbidden_terms_absent": true, "retrieved_source_present": true, "valid_citation_present": true}`

来源：`["星桥项目报告截止日期是2026年11月20日。"]`

### 3. 数据库回忆

问题：星桥项目默认使用什么数据库？

状态：completed；规则通过：True。

星桥项目默认使用 PostgreSQL。【来源1】

规则检查：`{"expected_terms": true, "forbidden_terms_absent": true, "retrieved_source_present": true, "valid_citation_present": true}`

来源：`["星桥项目默认数据库是PostgreSQL。", "星桥项目报告的默认输出语言是中文。", "星桥项目报告截止日期是2026年11月20日。"]`

### 4. 中文改写

问题：给星桥团队写报告时，文字应该采用哪种语言？

状态：completed；规则通过：True。

根据检索证据，星桥项目报告的默认输出语言是中文，所以给星桥团队写报告时，文字应采用中文【来源1】。

规则检查：`{"expected_terms": true, "forbidden_terms_absent": true, "retrieved_source_present": true, "valid_citation_present": true}`

来源：`["星桥项目报告的默认输出语言是中文。", "星桥项目报告截止日期是2026年11月20日。", "星桥项目报告导出格式是PDF。", "星桥项目默认数据库是PostgreSQL。"]`

### 5. 多事实组合

问题：请同时列出星桥项目报告截止日期、默认数据库和导出格式。

状态：completed；规则通过：False。

根据现有证据，只能确认一项：

- 星桥项目默认数据库：PostgreSQL【来源1】

其他两项证据不足，无法确认：
- 报告截止日期：未提供相关信息
- 导出格式：未提供相关信息

因此目前无法同时列出这三项内容。

规则检查：`{"expected_terms": false, "forbidden_terms_absent": true, "retrieved_source_present": true, "valid_citation_present": true}`

来源：`["星桥项目默认数据库是PostgreSQL。"]`

### 6. 未知预算拒猜

问题：星桥项目预算具体是多少元？没有记录就明确说明。

状态：completed；规则通过：True。

检索证据中没有关于星桥项目预算金额的记录，无法回答具体是多少元。

规则检查：`{"expected_terms": true, "forbidden_terms_absent": true, "refusal_marker": true}`

来源：`["星桥项目报告截止日期是2026年11月20日。", "星桥项目报告导出格式是PDF。", "星桥项目默认数据库是PostgreSQL。", "星桥项目报告的默认输出语言是中文。"]`

### 7. 项目隔离

问题：云帆项目默认使用什么数据库？没有记录就明确说明。

状态：completed；规则通过：True。

目前没有关于云帆项目默认数据库的记录，无法确认它使用什么数据库。

规则检查：`{"expected_terms": true, "forbidden_terms_absent": true, "sources_empty": true, "refusal_marker": true}`

来源：`[]`

### 8. 用户隔离

问题：星桥项目默认使用什么数据库？没有记录就明确说明。

状态：completed；规则通过：True。

现有记录中没有关于“星桥”项目默认使用什么数据库的信息，因此无法确定，不能猜测。

规则检查：`{"expected_terms": true, "forbidden_terms_absent": true, "sources_empty": true, "refusal_marker": true}`

来源：`[]`

### 9. 更正后回忆

问题：星桥项目报告的当前截止日期是什么？

状态：completed；规则通过：False。

星桥项目报告的当前截止日期是 **2026年11月20日**。【来源1】

规则检查：`{"expected_terms": true, "forbidden_terms_absent": false, "corrected_date": false, "retrieved_source_present": true, "valid_citation_present": true}`

来源：`["星桥项目报告截止日期是2026年11月20日。"]`

### 10. 更正后中文改写

问题：星桥报告现在最晚应当在哪一天交付？

状态：completed；规则通过：False。

星桥项目报告的最晚交付日期是 **2026年11月20日**。【来源1】

这是项目设定的截止日期。当前状态和计划中未显示有其他变更，因此以该截止日期为准。

规则检查：`{"expected_terms": true, "forbidden_terms_absent": false, "corrected_date": false, "retrieved_source_present": true, "valid_citation_present": true}`

来源：`["星桥项目报告截止日期是2026年11月20日。", "星桥项目报告导出格式是PDF。", "星桥项目报告的默认输出语言是中文。", "星桥项目默认数据库是PostgreSQL。"]`

## 复现与限制

```powershell
conda run --no-capture-output -n simplemem-agentmemory python scripts/live_quality.py
```

重跑会产生模型服务费用。答案具有随机性，失败记录保留，不以重试择优。
未测试真实宿主、公开长期对话集或真实用户数据；10题不支持统计推断或模型排名。
完整抽取内容、显式维护动作、来源与检查规则见同名 JSON。
