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

本节适用于仓库内的 Python、TypeScript、测试、脚本、配置和知识数据处理代码，是 Agent 执行编码任务时的完整规范。`specs/coding-standards.md` 保留规范原稿；修改规则时必须同步更新两处，出现冲突时以本节为准。

本文中的“必须”“禁止”为合并到 `main` 的硬性要求；“建议”为默认实践，偏离时必须记录例外。规则只有同时具备工具配置、CI 检查和分支保护时才视为已自动执行。

术语约定：

- 公开 Python API 指包级 `__all__` 导出的符号；未定义 `__all__` 时，指模块中不以下划线开头的模块、类、函数和方法。测试函数、fixture 和下划线前缀符号不属于公开 API。
- 公开 TypeScript API 指从包入口或模块中 `export` 的类型、函数、类、组件和常量。
- 外部输入包括 HTTP 响应、文件、环境变量、命令行参数、消息、数据库记录、第三方 SDK 返回值和 AI 输出。
- 魔法值指参与业务分支、状态流转、协议映射、配置查找，或在两个以上生产代码位置重复使用，却没有具名定义的字符串或数字。
- 生产代码指会被运行、打包或部署的代码，不包括规范文档、测试数据和专门验证扫描器的脱敏 fixture。
- 真实外部写操作指会改变 GitHub、Hacker News、Telegram、飞书或其他外部系统状态的请求。

通用要求：

- 解析、校验、业务逻辑、持久化和分发必须分层；函数和模块保持单一、清晰的职责。
- 所有外部输入必须先转换为受信任的内部类型，再用于业务逻辑、文件路径、持久化或外部写操作。
- 公共接口、关键数据结构、外部输入和返回值必须有明确类型。跨模块数据优先使用 `dataclass`、`TypedDict`、TypeScript interface/type 或显式 schema。
- AI 输出一律视为不可信输入，禁止直接将其作为代码、Shell 命令、文件路径、模板指令或外部写操作执行。
- 同一业务概念必须只有一个权威定义。状态、来源、渠道、字段名和阈值不得在 Python、TypeScript、schema 与测试中维护会漂移的副本。
- 采集、分析、整理和分发必须支持幂等执行；任务重跑不得静默产生重复记录或重复消息。

Python：

- 使用 Black 格式化并遵循 PEP 8；两者冲突时以 Black 输出为准。Black 版本必须锁定，配置集中放在 `pyproject.toml` 并指定 Python 3.12 目标版本。
- 导入按标准库、第三方库、项目内部模块分组，禁止通配符导入。
- Python 模块、源文件、函数和变量使用 `snake_case`；类使用 `PascalCase`；常量使用 `UPPER_SNAKE_CASE`。知识条目数据文件使用整理 Agent 定义的 `{date}-{source}-{slug}.json` 命名规范。
- 公开函数必须标注全部参数和返回值类型；关键内部函数和数据边界也必须有类型。优先使用 Python 3.12 原生类型语法。
- `Any` 只能出现在无法直接建模的输入边界，并在校验后尽快收窄。类型忽略必须限定具体错误码，并在同一行说明原因。
- 公开模块、类、函数和方法必须使用 Google 风格 docstring，按实际契约说明用途、参数、返回值、可预期异常、边界和副作用。
- 禁止裸 `print()`；统一使用标准库 `logging` 或项目 logger，并使用参数化日志消息。

TypeScript：

- TypeScript 规则仅在仓库存在 TypeScript 源码或 `tsconfig.json` 时启用；没有 TypeScript 项目时不得创建空工程应付检查。
- `tsconfig.json` 必须设置 `"strict": true`，不得关闭 `strictNullChecks`、`noImplicitAny` 等 strict 子选项规避问题。
- 公开函数、组件属性、外部数据结构和跨模块接口必须声明类型；公开 API 必须有 TSDoc。
- 使用 `unknown` 接收不可信输入，经类型守卫或 schema 校验后再使用；禁止无理由使用 `any`、非空断言或不安全类型断言。
- 对可辨识联合类型执行穷尽检查，使新增状态能够在编译期暴露遗漏分支。
- Prettier、ESLint、TypeScript 及插件版本必须锁定，配置集中存放，禁止仅在本机忽略规则。

常量、配置与依赖：

- 魔法值必须提取为常量、枚举、配置项或 schema 定义。普通文案、参数化日志模板、测试输入和只出现一次且不参与业务判断的协议字面量可以保留在使用点。
- 环境差异配置通过环境变量或批准的配置系统注入，不得硬编码密钥、账号、内部凭据或机器专属绝对路径。
- 配置必须在程序启动或任务入口处校验类型、范围和必填项；缺失或无效配置应快速失败。
- 新增第三方依赖前必须说明必要性，优先使用标准库或已有依赖，并在统一依赖文件中声明版本约束。
- 运行时依赖和开发、测试依赖必须区分；CI 使用锁文件或可复现的受约束版本安装。

网络、异常与日志：

- 每个网络请求必须设置显式超时；客户端支持时分别配置连接和读取超时，否则配置总超时。
- 默认只重试连接失败、读取超时、HTTP 429 和 HTTP 5xx。除 408、429 以及幂等冲突场景下的 409 外，HTTP 4xx 不得自动重试。
- 解析失败或 schema 不匹配默认不得重试；仅当来源已知会短暂返回不完整响应，且有测试和注释说明时，才允许有限重试。
- 重试必须同时具备最大尝试次数和最大总等待时间，使用指数退避与随机抖动；服务端提供有效 `Retry-After` 时应遵循，但不得突破总等待上限。
- GET、HEAD 等幂等读取可以重试。POST、PATCH、发送消息等写操作只有使用服务端支持的幂等键，或能确认前次未生效时才可重试。
- 禁止空 `except`、吞掉异常或返回含义不明的默认成功值。不可恢复错误应尽早失败并通过异常链保留原因；调用方只捕获能够处理的具体异常。
- 日志应包含可定位问题的对象标识和处理阶段，但不得记录凭据、完整敏感响应或个人敏感信息。第三方异常可能含有敏感 URL 或响应体时，必须改为记录脱敏字段。

数据与 schema：

- 机器可读知识条目 schema 应存放在 `schemas/knowledge_article.schema.json`，使用 JSON Schema，并通过 `schema_version` 字段版本化。
- schema 建立前，以本文件“知识条目 JSON 格式”的字段契约为准；建立后，字段枚举、必填性和类型只在 schema 中定义，文档只保留解释和示例。
- schema 破坏性变更必须提高主版本并提供可审计迁移脚本和回滚方案；兼容性新增字段提高次版本。
- `knowledge/articles/` 只能写入通过目标 schema 校验的 UTF-8 `.json` 文件。
- `knowledge/raw/` 原则上只追加。写入时先在同目录创建临时文件、刷新并原子重命名，避免进程中断产生半文件。
- 原始记录必须保存规范化来源 URL、采集时间和内容哈希。路径冲突时比较稳定 ID 与内容哈希：内容相同视为幂等成功，内容不同则写入带版本标识的新文件并记录冲突，禁止静默覆盖。
- 稳定 ID 必须由规范化后的稳定输入生成，不得使用运行时随机值；规范化算法变化时必须提供兼容或迁移策略。
- 摘要、标签、评分和分析结论必须能追溯到原始文件、来源 URL、分析模型和分析时间；无法核实的值使用 `null` 或明确标记为推断。

分发与 dry-run：

- 本地开发和测试默认启用 dry-run。dry-run 可以读取获准输入和生成待发送 payload，但不得调用外部写接口、上传附件、更新远端状态或把本地条目推进为 `published`。
- dry-run 输出也必须脱敏，不得泄露收件人隐私、凭据或不应进入日志的完整消息内容。
- 每个目标渠道必须单独记录 `pending`、`published` 或 `failed` 状态、幂等键、外部消息 ID、尝试次数、最后尝试时间和脱敏错误摘要。
- 只有所有选定渠道均成功时，条目总状态才可推进为 `published`。部分渠道成功时只重试失败渠道，不得重复发送成功渠道。
- 真实分发必须具有明确授权，且授权限定环境、渠道和任务范围；模型输出不得自行扩大权限。

测试与 CI：

- 单元测试总体覆盖率必须不低于 80%并启用分支统计；新增或变更代码行覆盖率必须不低于 90%。覆盖范围只统计生产代码，排除测试、生成代码、缓存和第三方代码。
- 状态流转、schema 校验、稳定 ID、原子写入、重试边界、幂等分发和权限检查必须覆盖关键分支；覆盖率数字不能替代有效断言。
- Bug 修复必须先补充或同时补充能够复现问题的回归测试。
- 测试必须可重复、相互隔离，不依赖执行顺序、真实时间、随机数、网络结果或开发者本机状态；时间和随机性通过注入、mock 或固定 seed 控制。
- 单元测试禁止所有真实公网访问，包括只读请求。外部调用使用 mock、fixture 或录制的脱敏响应；集成测试必须显式标记且只连接批准的沙箱。
- CI 默认任务不得持有生产写凭据；测试数据必须最小化，不得包含真实凭据或敏感信息。
- Python 统一使用 Black、Ruff、mypy、pytest、pytest-cov、coverage.py 和 diff-cover；TypeScript 统一使用 Prettier、ESLint 和 TypeScript compiler。所有工具版本必须锁定。
- CI 至少执行与以下命令等价的 Python 检查：

```bash
python -m black --check .
python -m ruff check .
python -m mypy .
python -m pytest --cov --cov-branch --cov-report=xml --cov-report=term-missing --cov-fail-under=80
diff-cover coverage.xml --fail-under=90
```

- 存在 TypeScript 项目时，CI 还必须执行 formatter check、ESLint 和 `npx tsc --noEmit`。
- CI 必须执行 schema/data contract 测试、禁止项扫描和凭据扫描。所有检查由单一任务入口封装，本地与 CI 调用同一入口。
- `main` 必须启用分支保护、禁止直接推送，并要求所有 CI checks 和至少一名 reviewer 通过。管理员绕过必须保留审计记录。

禁止项与例外：

- 禁止将 `TODO`、`FIXME`、`HACK`、`XXX` 或等价的未完成标记提交到 `main`；后续工作必须创建可追踪任务，当前变更保持完整可用。
- 禁止注释掉的死代码、临时调试代码、生成缓存、日志文件或本机环境文件进入 `main`。
- 禁止裸 `print()`、空 `except`、无限循环、无限重试和无超时网络调用。
- 禁止在代码、测试、fixture、文档、日志或提交记录中存放真实凭据。
- 禁止把未经 schema 校验的数据写入 `knowledge/articles/`，或把失败、拒绝、部分分发成功及未经审核的条目标记为 `published`。
- 禁止项扫描只针对项目维护的生产代码和配置。规范文档、第三方或生成代码以及专门测试扫描器的脱敏 fixture 可以通过集中配置排除，生产代码不得临时绕过。
- 临时例外必须记录规则、最小适用范围、原因、风险、负责人、关联任务和明确到日期的移除期限，并由 reviewer 显式批准。过期例外视为 CI 失败，不得自动延期。
- 凭据安全、数据可追溯性、未经授权的外部写操作和 `knowledge/raw/` 禁止静默覆盖等红线不接受例外。

当前仓库尚未配置完整工具链时，依次建立统一依赖与 `pyproject.toml`、机器可读 schema、单一检查入口和 CI，再启用 `main` 分支保护。TypeScript 工具链只在接入 TypeScript 后配置。工具链全部通过后，以上硬性要求作为不可绕过的合并门槛。

## 项目结构

```text
.
├── .codex/
│   ├── agents/             # Agent 的角色定义、提示词与权限边界
│   │   ├── analyzer.md     # 只读知识分析 Agent
│   │   ├── collector.md    # 只读知识采集 Agent
│   │   ├── organizer.md    # 知识整理与持久化 Agent
│   │   └── reviewer.md     # 知识质量审核 Agent
│   └── skills/             # 可复用技能及其说明、脚本和资源
├── knowledge/
│   ├── raw/                # 原始采集结果；保留来源信息，原则上只追加
│   └── articles/           # 分析、去重和标准化后的知识条目 JSON
├── specs/
│   └── coding-standards.md # 编码规范原稿
└── AGENTS.md
```

- `.codex/agents/` 中每个角色应职责单一，明确输入、输出、允许与禁止权限以及失败处理方式。
- `.codex/agents/collector.md` 定义只读的知识采集 Agent；它返回包含稳定 ID、真实采集时间和原始热度证据的候选 JSON，不直接写入知识库。
- `.codex/agents/analyzer.md` 定义只读的知识分析 Agent；它生成摘要、亮点、证据、限制、`1-10` 评分、建议标签和状态建议，不直接写入知识库。
- `.codex/agents/organizer.md` 定义知识整理 Agent；它只接受显式 JSON 交接，负责去重、schema 或字段契约校验，以及 `knowledge/articles/` 内的受限写入。
- `.codex/agents/reviewer.md` 定义知识审核 Agent；它基于本地证据执行质量打分、硬门槛、异常与合规检查，并把审核结果写回知识条目。
- `.codex/skills/` 中的技能应可独立复用，不得把密钥或环境专属配置写入技能文件。
- `knowledge/raw/` 保存可复现分析过程所需的原始数据；采集器不得在此阶段生成未经标识的 AI 推断内容。
- `knowledge/articles/` 仅保存通过 schema 校验的知识条目。文件应使用 UTF-8 编码和 `.json` 后缀。
- `specs/coding-standards.md` 保留编码规范原稿；本文件“编码规范”是 Agent 执行时的完整规则，修改时必须同步更新两处。

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
  "status": "ready",
  "score": 0.91,
  "raw_file": "knowledge/raw/github_trending/2026-07-14/example-ai.json",
  "popularity": 95,
  "popularity_raw": 14650,
  "popularity_unit": "stars_this_week",
  "popularity_method": "linear_relative_to_batch_max",
  "source_metrics": {
    "stars_total": 48200,
    "forks_total": 3200,
    "period_stars": 14650,
    "period": "weekly",
    "period_days": 7,
    "stars_daily_avg_estimated": 2092.86,
    "rank": 1,
    "description": "来源页面的项目简介",
    "readme_summary": "基于 README 的中文摘要",
    "primary_language": "Python",
    "topics": ["llm", "agent"],
    "license": "MIT",
    "updated_at": "2026-07-14T01:20:00Z",
    "recent_activity": {
      "pushed_at": "2026-07-14T00:50:00Z",
      "commits_30d": null,
      "method": "repository_pushed_at"
    },
    "compliance_evidence": ["公开许可与用途检查依据"]
  },
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
  },
  "quality_review": {
    "score": 82,
    "tier": "quality",
    "decision": "accepted",
    "hard_gate_passed": true,
    "hard_gate_failures": [],
    "dimensions": {
      "popularity": {"score": 30, "max_score": 35, "evidence": "同批次 weekly stars 增量及批次最大值"},
      "maturity": {"score": 16, "max_score": 20, "evidence": "总 stars、fork 和提交活跃度"},
      "information_completeness": {"score": 18, "max_score": 20, "evidence": "简介、README 摘要、语言和 topics"},
      "scarcity_value": {"score": 8, "max_score": 15, "evidence": "与同批次项目对比后的差异化判断"},
      "compliance": {"score": 10, "max_score": 10, "evidence": "本地合规检查依据"}
    },
    "anomaly_flags": [],
    "missing_inputs": [],
    "review_version": "1.1",
    "reviewed_at": "2026-07-14T10:00:00+08:00"
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
- GitHub 采集结果必须保留 `popularity_raw`、`popularity_unit` 和 `source_metrics`，使 Reviewer 可在不联网的情况下复算热度、成熟度、完整性与合规分。
- `stars_daily_avg_estimated` 只表示 `period_stars / period_days` 的窗口估算日均，不是真实连续 7 日历史均值，不得用于冒充历史趋势证据。
- Organizer 写入的新条目保持 `analyzed`；Reviewer 审核分数不低于 60 且通过全部硬门槛后才可推进到 `ready`。
- `quality_review.score` 为 `0-100` 的综合质量分，与顶层 `score` 的 `0.0-1.0` 内容价值分相互独立，不得混用。
- `quality_review` 的五个维度满分依次为 35、20、20、15、10；合规干净度必须为 `10/10`，硬门槛失败时条目标记为 `rejected`。
- 状态按 `collected -> analyzed -> ready -> published` 推进；40–59 分保持 `analyzed` 等待复核，低于 40 分或不符合硬门槛的条目标记为 `rejected`，处理失败且需要排查的条目标记为 `failed`。

## Agent 角色概览

采集任务使用 [知识采集 Agent](.codex/agents/collector.md)，分析任务使用 [知识分析 Agent](.codex/agents/analyzer.md)，整理与持久化任务使用 [知识整理 Agent](.codex/agents/organizer.md)，质量准入使用 [知识审核 Agent](.codex/agents/reviewer.md)。采集和分析角色只读并返回 JSON；整理角色写入 `analyzed` 条目，审核角色打分后决定是否推进到 `ready`。

| 角色 | 主要职责 | 输入 | 输出 |
| --- | --- | --- | --- |
| [采集 Agent](.codex/agents/collector.md) | 只读搜索 GitHub Trending 和 Hacker News，同次提取来源、窗口热度、成熟度、完整性与合规审核证据，初步筛选、去重并排序 | 来源配置、采集时间窗、已有条目 ID/URL | 包含稳定 ID、真实采集时间、原始热度及 `source_metrics` 的 JSON 数组；默认至少 15 条，用户指定 Top N 时以 N 为准 |
| [分析 Agent](.codex/agents/analyzer.md) | 只读分析明确指定的 raw 数据，生成摘要、亮点、证据、限制、评分、标签和状态建议 | raw 文件或完整 JSON、分析规则、历史标签 | 包含分析元数据和 `recommended_status` 的显式 JSON 数组；不直接写入文件 |
| [整理 Agent](.codex/agents/organizer.md) | 接收显式 raw 与分析 JSON，检查重复、校验数据契约并分类写入知识库 | raw 文件、分析 JSON、历史知识库、可用 schema | 按 `{date}-{source}-{slug}.json` 写入状态为 `analyzed` 的条目，并返回含原因、校验依据和警告的处理清单 |
| [审核 Agent](.codex/agents/reviewer.md) | 读取本地知识条目，执行硬门槛、异常与合规检查，按五个维度计算质量分 | `knowledge/articles/`、对应 raw、本地指标和审核规则 | 写回 `quality_review`；合格条目推进到 `ready`，待复核保持 `analyzed`，废弃条目标记为 `rejected` |

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
