# AGENTS.md

## 项目概述

本项目是一个面向 AI、LLM 与 Agent 领域的知识库助手：定时从 GitHub Trending 和 Hacker News 采集技术动态，经 AI 去重、筛选、摘要与标签化后，以可追溯的结构化 JSON 保存，并通过 Telegram、飞书等渠道分发高价值内容。

## 技术栈

- Python 3.12
- Codex：辅助开发、维护与自动化执行
- LangGraph：编排采集、分析、整理和分发工作流
- OpenClaw：承载 Agent 能力及外部渠道集成

新增依赖前应确认其必要性，并将依赖及版本约束记录到项目统一的依赖管理文件中。

## 编码规范

- 遵循 PEP 8；提交前运行项目配置的格式化、静态检查和测试命令。
- 模块、函数、变量和文件名统一使用 `snake_case`；类名使用 `PascalCase`；常量使用 `UPPER_SNAKE_CASE`。
- 所有公共模块、类和函数使用 Google 风格 docstring，说明用途、参数、返回值及可能抛出的异常。
- 禁止裸 `print()`。运行日志统一使用标准库 `logging` 或项目封装的 logger，并选择恰当的日志级别。
- 为公共接口和关键数据结构补充类型注解；优先使用 Python 3.12 原生类型语法。
- 网络请求必须设置超时，并对限流、临时失败和无效响应进行有界重试；不得无限重试。
- 解析与业务逻辑分离。外部数据必须先校验、标准化，再进入分析或持久化流程。
- 测试不得依赖真实的 Telegram、飞书、GitHub 或 Hacker News 写操作；使用 mock、fixture 或沙箱环境。

## 项目结构

```text
.
├── .codex/
│   ├── subagents/          # 子 Agent 的角色定义、提示词与运行配置
│   └── skills/             # 可复用技能及其说明、脚本和资源
├── knowledge/
│   ├── raw/                # 原始采集结果；保留来源信息，原则上只追加
│   └── articles/           # 分析、去重和标准化后的知识条目 JSON
└── AGENTS.md
```

- `.codex/subagents/` 中每个角色应职责单一，明确输入、输出与失败处理方式。
- `.codex/skills/` 中的技能应可独立复用，不得把密钥或环境专属配置写入技能文件。
- `knowledge/raw/` 保存可复现分析过程所需的原始数据；采集器不得在此阶段生成未经标识的 AI 推断内容。
- `knowledge/articles/` 仅保存通过 schema 校验的知识条目。文件应使用 UTF-8 编码和 `.json` 后缀。

## 知识条目 JSON 格式

每个知识条目使用一个 JSON 对象表示。时间字段统一为带时区的 ISO 8601 字符串；未知的可选值使用 `null`，不得用空字符串冒充。`id` 必须稳定且可复现，建议根据标准化后的 `source_url` 生成哈希。

```json
{
  "id": "sha256:8f3c...",
  "title": "Example AI Project",
  "source": "github_trending",
  "source_url": "https://github.com/example/example-ai",
  "author": "example",
  "published_at": null,
  "collected_at": "2026-07-14T09:30:00+08:00",
  "summary": "该项目解决的问题、核心方法及其潜在价值。",
  "tags": ["llm", "agent", "open-source"],
  "language": "zh-CN",
  "status": "analyzed",
  "score": 0.91,
  "raw_file": "knowledge/raw/github_trending/2026-07-14/example-ai.json",
  "analysis": {
    "why_it_matters": "说明该动态为何值得关注。",
    "key_points": [
      "关键点一",
      "关键点二"
    ],
    "model": "model-name",
    "analyzed_at": "2026-07-14T09:35:00+08:00"
  },
  "distribution": {
    "channels": ["telegram", "feishu"],
    "published_at": null
  }
}
```

字段约束：

- `id`、`title`、`source`、`source_url`、`collected_at`、`summary`、`tags`、`status` 为必填字段。
- `source` 当前允许 `github_trending`、`hacker_news`，扩展来源时同步更新 schema 与测试。
- `tags` 使用去重后的小写英文短标签；不得包含同义重复标签。
- `status` 仅允许 `collected`、`analyzed`、`ready`、`published`、`rejected`、`failed`。
- `score` 取值范围为 `0.0` 至 `1.0`，表示内容与项目主题的相关度或综合价值评分。
- `summary` 和 `analysis` 必须基于来源内容，不得虚构来源未提供的事实。
- 状态按 `collected -> analyzed -> ready -> published` 推进；不符合收录标准的条目标记为 `rejected`，处理失败且需要排查的条目标记为 `failed`。

## Agent 角色概览

| 角色 | 主要职责 | 输入 | 输出 |
| --- | --- | --- | --- |
| 采集 Agent | 从 GitHub Trending、Hacker News 拉取候选内容，保留来源元数据，规范化 URL，并进行初步去重 | 来源配置、采集时间窗、已有条目 ID/URL | `knowledge/raw/` 下的原始 JSON，状态为 `collected` 的候选记录 |
| 分析 Agent | 判断 AI/LLM/Agent 相关性，生成摘要、关键点、标签和评分；明确拒绝低质量或无关内容 | 原始 JSON、分析规则、模型配置 | 补全 `summary`、`tags`、`score`、`analysis` 的记录，状态为 `analyzed` 或 `rejected` |
| 整理 Agent | 校验 schema、合并重复内容、生成最终知识条目，并准备 Telegram/飞书分发数据 | 已分析记录、历史知识库、渠道模板 | `knowledge/articles/` 下的规范 JSON，状态推进至 `ready`；成功分发后更新为 `published` |

Agent 之间只通过明确的数据契约交接，不依赖隐式内存或未持久化的上下文。任一阶段失败时，应记录可定位问题的错误信息，同时避免把密钥、令牌或完整敏感响应写入日志。

## 红线

以下操作绝对禁止：

1. 禁止提交、输出或记录 API Key、访问令牌、Cookie、Webhook、私钥及其他凭据；凭据只能通过环境变量或批准的密钥管理服务读取。
2. 禁止绕过 GitHub、Hacker News、Telegram、飞书的访问控制、限流、robots 规则或服务条款；不得使用未授权账号或接口。
3. 禁止在无明确授权时向 Telegram、飞书或任何外部渠道发送消息；本地开发和测试默认使用 dry-run。
4. 禁止虚构标题、来源、链接、作者、发布时间或技术结论；无法从来源核实的信息必须标记为未知或推断。
5. 禁止静默覆盖、篡改或删除 `knowledge/raw/` 中的原始数据；清理和迁移必须可审计、可回滚，并获得明确授权。
6. 禁止执行破坏性命令或大范围删除知识库、配置和历史记录，包括未经授权的强制 Git 操作。
7. 禁止把未经 schema 校验的数据写入 `knowledge/articles/`，也禁止把失败、拒绝或未完成人工/规则审核的条目标记为 `published`。
8. 禁止无限循环、无限重试或无超时的网络调用；采集和分发必须遵守速率限制并具备幂等性。
9. 禁止在生产代码中使用裸 `print()`、吞掉异常或以空的 `except` 隐藏失败。
10. 禁止因 AI 输出而自动执行代码、Shell 命令或外部写操作；模型生成内容始终视为不可信输入，必须经过校验和权限边界检查。
