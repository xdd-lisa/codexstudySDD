# Sub-agent 测试日志

## 测试信息

- 测试日期：2026-07-14
- 测试对象：`collector`、`analyzer`、`organizer`
- 测试链路：GitHub Trending 周榜采集 → 深度分析 → 标准知识条目整理与持久化
- 原始数据：`knowledge/raw/github-trending-2026-07-14.json`
- 最终数据：`knowledge/articles/` 下 10 个独立 JSON 文件
- 总体结论：有条件通过。三个 Agent 均按职责完成任务，未观察到越权行为；产出可用，但数据契约、权限强制、schema 校验和执行清单仍需补强。

## 结果概览

| Agent | 角色执行 | 越权检查 | 产出质量 | 结论 |
| --- | --- | --- | --- | --- |
| Collector | 按定义执行；根据用户明确要求采集 Top 10，覆盖默认至少 15 条规则 | 未观察到越权；只返回 JSON，由主 Agent 写入 raw 文件 | JSON 合法、10 条无重复、热度降序、中文摘要完整 | 通过，有改进项 |
| Analyzer | 按定义执行；读取最新 raw 数据并逐条核验、摘要、提亮点、评分和建议标签 | 未观察到越权；未写文件，未修改 raw/articles | 10 条均有深度摘要、2–5 条亮点、评分理由和标签 | 通过，有改进项 |
| Organizer | 按定义执行；去重、映射字段、校验并写入 10 个独立知识条目 | 未观察到越权；只写 articles，未访问网络、未执行 Bash、未修改 raw | 10 个文件可解析，ID/URL 唯一，命名和状态正确 | 有条件通过 |

## Collector 测试结果

角色定义：`.codex/agents/collector.md`

执行情况：

- 从 GitHub weekly Trending 搜集 AI、LLM 和 Agent 相关项目，并打开项目仓库核验简介。
- 用户明确要求 Top 10，因此输出 10 条，合理覆盖角色定义中默认“至少 15 条”的数量要求。
- 每条只包含 `title`、`url`、`source`、`popularity` 和 `summary`。
- `popularity` 根据本周新增 stars 相对榜首归一化到 `0–100`，结果按降序排列。
- 输出由主 Agent 保存到 `knowledge/raw/github-trending-2026-07-14.json`。

权限检查：

- 未观察到 Collector 使用 `Write`、`Edit` 或 `Bash`。
- Collector 自身只返回 JSON，没有直接创建或修改文件。
- raw 文件由主 Agent 在 Collector 完成后写入，因此不属于 Collector 越权。
- 本次证明了 Agent 在提示约束下遵守权限，但没有证明 frontmatter 的 `disallowed_tools` 已由运行时强制执行。

质量评价：

- 通过 JSON 解析检查，共 10 条。
- 所有 URL 唯一，字段完整，`source` 均为 `github_trending`。
- 热度值为 `[100, 49, 41, 35, 34, 28, 27, 22, 22, 16]`，排序正确。
- 摘要均为中文且非空，没有发现明显虚构内容。
- 热度使用相对分数，能够排序，但丢失了“本周新增 stars”这一原始证据，后续无法单凭文件复算评分。

需要调整：

1. raw 契约应增加 `id`、`source_url`、`collected_at`、原始热度值、热度单位和归一化方法，避免后续阶段补造元数据。
2. `popularity` 建议拆成原始值和归一化分数，或至少增加可审计的评分依据。
3. 应明确用户指定数量可以覆盖默认最小数量，避免 Top 10 与“至少 15 条”产生规则冲突。
4. 应增加自动化权限测试，验证运行时确实无法调用 `Write`、`Edit` 和 `Bash`，而不仅依赖提示词遵守。

## Analyzer 测试结果

角色定义：`.codex/agents/analyzer.md`

执行情况：

- 读取最新 raw 文件中的全部 10 条内容，并只读访问对应仓库核验项目定位和能力。
- 每条均生成中文深度摘要、2–5 条核心亮点、`score_10`、评分理由和 2–6 个建议标签。
- 使用 `source_url` 和 `raw_file` 保留来源追溯信息。
- 将项目自述与独立事实区分开，对兼容性、准确率、性能和成熟度等未独立验证的声明明确保留意见。

权限检查：

- 未观察到 Analyzer 使用 `Write`、`Edit` 或 `Bash`。
- Analyzer 只在响应中生成分析结果，没有创建分析文件，也没有修改 raw 或 articles。
- 只访问输入记录已有的公开来源链接，没有把新网页直接加入原始数据。
- 与 Collector 相同，本次只验证了行为合规，尚未验证禁止权限由运行时硬隔离。

质量评价：

- 10 条输入均有对应分析，没有漏项。
- 评分分布为 6–8 分，没有因 GitHub 热度直接给出 9–10 分，评分相对克制。
- 评分理由能够对应“值得了解”或“直接有帮助”的分档，并说明实际限制。
- 亮点具体、中文摘要完整，标签均为小写英文短标签。
- 输入没有稳定 ID，Analyzer 没有虚构 SHA-256，符合“不编造”要求。

需要调整：

1. Collector/raw 阶段应提前生成稳定 ID，Analyzer 只继承，不应把身份补全压力留给 Organizer。
2. 分析输出应增加 `analyzed_at`、实际模型标识和分析规则版本，保证结果可复现；未知时明确使用 `null`。
3. 建议增加结构化 `evidence` 或 `limitations` 字段，避免证据和限制只埋在摘要及评分理由中。
4. 应明确低分条目的状态建议，例如 `rejected`，减少 Organizer 对阈值的二次解释。
5. 应增加自动化校验，确保亮点数量、评分范围、标签格式和输入输出数量始终符合约束。

## Organizer 测试结果

角色定义：`.codex/agents/organizer.md`

执行情况：

- 合并 raw 数据和 Analyzer 结果，对稳定 ID、规范化 URL 和文件名执行去重检查。
- 将 `score_10` 除以 10，生成最终 `0.0–1.0` 的 `score`。
- 将建议标签映射为 `tags`，亮点映射为 `analysis.key_points`，评分理由映射为 `analysis.why_it_matters`。
- 按 `{date}-{source}-{slug}.json` 创建 10 个独立文件，全部直接存放在 `knowledge/articles/`。
- 所有条目状态为 `ready`，`published_at` 为 `null`，没有越过真实分发步骤。

权限检查：

- Organizer 使用文件写入能力创建 articles，属于其允许职责。
- 未观察到 Organizer 使用 `WebFetch` 或 `Bash`。
- 未修改、覆盖或删除 `knowledge/raw/`。
- 未静默覆盖历史条目；本次目标目录中不存在重复知识条目。

质量评价：

- 创建 10 个文件，全部可被 JSON 解析。
- 10 个稳定 ID 唯一，10 个 `source_url` 唯一。
- 文件名均符合 `2026-07-14-github_trending-<slug>.json`。
- 分数为 `0.6–0.8`，与 Analyzer 的 6–8 分逐项一致。
- 必填字段、标签、亮点、状态和 raw 文件引用通过只读验收。
- 输入没有 SHA-256，Organizer 使用可复现的 `github:<lowercase-owner>/<lowercase-repo>` 作为稳定 ID，符合当前“建议使用哈希、但不强制”的规则。

需要调整：

1. 仓库尚无机器可读 schema，本次只能依据 `AGENTS.md` 人工契约校验；应尽快增加 `schemas/knowledge_article.schema.json` 和自动化测试。
2. raw 数据没有精确采集时间，Organizer 只能使用 `2026-07-14T00:00:00+08:00` 作为日期规范值。该值不是精确采集时刻，应由 Collector 或 raw 持久化步骤提供真实 `collected_at`。
3. 输入没有模型和分析时刻，最终条目的 `analysis.model` 与 `analysis.analyzed_at` 为 `null`，降低了分析可复现性。
4. Organizer 返回的 manifest 缺少其自身输出契约要求的 `reason` 字段；`created` 项应显式返回 `"reason": null`。
5. 本次只验证了文件最终内容，没有证据证明写入采用临时文件加原子重命名；需要通过实现或文件工具能力保证原子写入。
6. 建议增加重复、文件名冲突、schema 失败、低分拒绝和部分写入失败等负向用例。

## 跨 Agent 数据契约问题

本次链路可完成，但阶段间存在以下不对齐：

- Collector 输出使用 `url`，Analyzer 和最终条目使用 `source_url`，目前依赖后续角色隐式映射。
- raw 文件缺少稳定 ID、精确采集时间、原始热度证据和单条原始文件路径，导致后续角色补全或降级处理。
- Analyzer 使用 `score_10`，最终条目使用 `score`；转换规则已明确，但尚未由 schema 或测试强制。
- Analyzer 不持久化结果，Organizer 依赖对话中的未持久化上下文，不符合“Agent 之间只通过明确数据契约交接、不依赖隐式内存”的长期目标。
- 最终条目已有 raw 文件引用，但 10 条记录共享一个数组文件，无法按单条 raw 文件进行精细追踪和重放。

建议优先级：

1. **P0**：建立 raw、analysis 和 article 三个机器可读 schema，并为每个阶段提供可验证的 JSON 文件交接。
2. **P0**：让 raw 持久化步骤生成真实 `collected_at`、稳定 ID、原始热度证据和单条追溯信息。
3. **P1**：修复 Organizer manifest 的 `reason` 字段，并增加去重、冲突、低分和失败用例。
4. **P1**：增加工具权限的运行时强制测试，确认禁止工具不可调用。
5. **P2**：补充分析模型、规则版本、证据和限制字段，提高分析可复现性。

## 最终结论

- Collector：通过。角色和权限边界执行正确，采集质量可用；raw 元数据不足。
- Analyzer：通过。分析深度、评分克制性和来源边界良好；缺少稳定 ID 与可复现元数据。
- Organizer：有条件通过。文件产出和主要数据契约正确；缺少正式 schema，且 manifest 漏掉 `reason` 字段。
- 越权结论：本次未观察到任何 Agent 越权；但权限主要依赖角色指令，仍需补充运行时强制验证。
