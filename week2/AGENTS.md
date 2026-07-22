# AGENTS.md

本文件是 `week2/` 项目的工程宪法，适用于本目录及其所有子目录。Agent 在修改代码、数据、配置或文档前必须先阅读并遵守本文件；如果子目录存在更具体的 `AGENTS.md`，则子目录文件可补充或收紧规则，但不得降低本文件的安全与质量要求。

## 1. 项目概述

本项目是一个可恢复的 AI 技术文章知识库流水线：从 GitHub 和 RSS 采集内容，经 LLM 分析后生成统一格式的正式文章，并通过本地 MCP 服务提供文章搜索、详情读取和统计能力。

核心目标：

- 从配置化数据源稳定采集 AI 技术内容；
- 使用统一 Article Schema 保存可校验、可追踪的文章数据；
- 单个来源或单篇文章失败时不中断整个批次；
- 支持检查点恢复，避免重复分析已完成项目；
- 通过 MCP 向 Codex 暴露只读知识库能力；
- 在数据规模证明有需要之前，保持实现简单、可测试、可维护。

## 2. 技术栈

### 运行时与语言

- Python 3.11 及以上版本；
- 项目元数据和依赖以 `pyproject.toml` 为准；
- Python 包采用根目录包与 `src/` 布局并存：流水线位于 `pipeline/`，共享领域包位于 `src/knowledge_base/`。

### 主要依赖

- `httpx[socks]`：GitHub、RSS 和 LLM HTTP 请求；
- `PyYAML`：读取 `pipeline/rss_sources.yaml`；
- Python 标准库：JSON、MCP stdio、文件存储、时间和检查点管理；
- 不使用数据库；当前持久化介质是 `knowledge/` 下的 JSON 文件。

### 测试与质量工具

- `unittest`：单元测试和流程回归；
- Ruff：格式、导入、静态质量检查；
- `.codex/hooks/scripts/validate_json.py`：正式文章 Schema 校验；
- `.codex/hooks/scripts/check_quality.py`：文章质量评分；
- Codex PostToolUse Hook：在相关文件读写后执行知识文章校验。

除非需求明确且有充分理由，不得引入新的框架、数据库、ORM、任务队列或索引服务。

## 3. 编码规范

### Python 风格

- 遵守 `pyproject.toml` 中的 Ruff 配置；
- 新代码必须包含准确的类型标注；
- 模块、函数、变量使用 `snake_case`，类使用 `PascalCase`，常量使用 `UPPER_SNAKE_CASE`；
- 公共模块、类和非显然函数必须提供简洁 docstring；
- 优先使用小函数、明确的数据流和显式异常，不使用隐藏副作用；
- 文件写入必须采用临时文件加原子替换，避免留下半写入 JSON；
- 捕获异常时使用最窄的异常类型，禁止无说明的 `except Exception`；
- 不得通过复制代码重新定义 Article Schema 或仓储规则。

### Article Schema

- `src/knowledge_base/schema.py` 是 Article Schema 的唯一事实源；
- 流水线、Hook、测试和 MCP 必须调用共享 Schema，禁止各自维护字段范围；
- 当前 Schema 版本为 `schema_version = 1`；
- Article ID 是来源 URL 生成的 16 位小写十六进制摘要；
- `score` 范围为 0–10；
- `published_at` 为 ISO 8601 或 `null`，`collected_at` 必须为 ISO 8601；
- `tags` 必须包含 1–5 个小写规范化标签；
- `analysis` 必须记录非空的 `provider` 和 `model`。

变更 Schema 时必须同步：迁移工具、文章数据、Hook、质量检查、MCP、测试夹具和 README。禁止只修改其中一个消费者。

### 正式文章命名

`knowledge/articles/` 中的文件名必须由 `knowledge_base.repository.article_filename()` 生成：

```text
{source}-{short-title}-{id前8位}.json
```

- 全部使用小写 ASCII 和连字符；
- `source` 最长 12 个字符；
- `short-title` 最长 32 个字符；
- 文件名最长 63 个字符；
- 完整标题和完整 ID 保存在 JSON 正文中；
- 禁止手工拼接另一套文件名规则。

### 禁止项

- 禁止把测试文章、临时 JSON、调试输出放入 `knowledge/articles/`；
- 禁止在源码、配置、测试或文档中提交 API Key、Token、Cookie 或内部凭证；
- 禁止直接覆盖正式文章或检查点文件；
- 禁止绕过 Schema 校验保存正式文章；
- 禁止把 MCP 业务实现放入 `pipeline/`；
- 禁止把共享领域契约放入 MCP 或流水线私有目录；
- 禁止在没有基准数据的情况下提前引入缓存、索引、数据库或分布式组件。

## 4. 项目结构

```text
week2/
├── AGENTS.md                         # 项目工程宪法
├── README.md                         # 使用说明和架构概览
├── pyproject.toml                    # 依赖、入口和工具配置
├── .env.example                      # 环境变量模板，不含真实凭证
├── .codex/
│   ├── config.toml                   # 项目级 Codex/MCP 配置
│   ├── hooks.json                    # Codex 自动发现的 Hook 配置
│   ├── hooks/scripts/                # Hook、Schema 校验和质量检查脚本
│   ├── mcp_servers/
│   │   ├── common/                   # 多个 MCP 可复用的协议辅助代码
│   │   └── local_knowledge/
│   │       ├── main.py               # MCP 启动入口
│   │       ├── server.py             # MCP 协议与 search/get/stats 工具
│   │       └── requirements.txt
│   └── skills/                       # 项目级 Codex Skills
├── src/knowledge_base/
│   ├── schema.py                     # 共享 Article Schema
│   └── repository.py                 # 共享正式文章仓储与命名规则
├── pipeline/
│   ├── collector.py                  # GitHub/RSS 采集和 YAML 读取
│   ├── model_client.py               # LLM 提供商适配
│   ├── pipeline.py                   # 分析、去重和流程编排
│   ├── storage.py                    # raw、failed、checkpoint 存储
│   ├── migrate.py                    # Schema 和文件名迁移
│   ├── rss_sources.yaml              # RSS 数据源配置
│   └── __main__.py                   # `python -m pipeline` 入口
├── knowledge/
│   ├── articles/                     # 仅存放通过 Schema 的正式文章
│   ├── raw/                          # 原始采集批次
│   ├── failed/                       # 按条目隔离的失败记录
│   └── checkpoint.json               # 运行时生成的恢复状态
└── tests/
    ├── fixtures/articles/            # 测试文章和固定输入
    ├── test_pipeline.py
    └── test_mcp_knowledge_server.py
```

目录职责规则：

- `pipeline/` 只放采集、分析、流程状态和编排代码；
- `.codex/mcp_servers/` 只放 MCP 启动、协议和工具实现；
- `src/knowledge_base/` 放流水线与 MCP 共享的领域契约和仓储；
- `tests/fixtures/` 放测试数据；
- `knowledge/articles/` 是生产数据目录，不是测试目录。

## 5. 工作流程

### 开发步骤

1. 阅读 `AGENTS.md`、`README.md` 和将要修改的模块；
2. 先确认变更所属层级，不跨目录混放职责；
3. Schema 或存储变更先更新共享契约，再更新消费者；
4. 为新增行为增加或更新测试；
5. 运行与风险匹配的验证命令；
6. 检查 `git diff` 和 `git status`，不得覆盖无关用户修改。

### 本地质量门禁

提交前至少运行：

```bash
python3 -m unittest discover -s tests -v
ruff check pipeline tests src .codex/mcp_servers/local_knowledge .codex/hooks/scripts
python3 .codex/hooks/scripts/validate_json.py 'knowledge/articles/*.json'
```

涉及文章质量算法时额外运行：

```bash
python3 .codex/hooks/scripts/check_quality.py knowledge/articles/*.json
```

涉及 MCP 时必须验证 MCP 入口和至少一个工具调用：

```bash
python3 .codex/mcp_servers/local_knowledge/main.py
```

### Git 与提交

- 未经用户明确要求，Agent 不得自行提交、推送、创建 PR 或切换分支；
- 推荐分支前缀：`feature/`、`fix/`、`refactor/`、`docs/`、`chore/`；
- 提交信息推荐使用 Conventional Commits，例如 `feat: add resumable rss collection`；
- 一个提交聚焦一个逻辑主题，Schema 变更及其迁移和测试必须放在同一提交；
- 禁止提交 `.env`、真实凭证、临时失败调试文件、编辑器缓存和无关二进制产物。

### CI/CD

当前仓库未声明正式 CI/CD 平台，因此不得假设 GitHub Actions、GitLab CI 或部署流水线已经存在。新增 CI 时，至少包含：

1. 安装 `pyproject.toml` 依赖；
2. 运行 Ruff；
3. 运行全部单元测试；
4. 校验全部正式文章；
5. 对 MCP 执行 stdio 初始化冒烟测试。

## 6. 特殊约束

### 安全

- 所有密钥仅通过环境变量读取，`.env.example` 只能包含空值或安全示例；
- 日志和失败文件不得记录 Authorization Header、API Key 或完整敏感响应；
- RSS/XML、GitHub 数据和 LLM 输出均视为不可信输入，必须校验类型、长度和格式；
- MCP 当前只暴露只读工具；新增写工具必须单独评审权限、确认机制和回滚方案；
- 文件路径必须限制在项目预期目录，防止路径遍历和任意文件覆盖。

### 可靠性

- 单个采集源失败不得阻断其他来源；
- 单篇分析、校验或保存失败不得阻断后续文章；
- 失败必须写入 `knowledge/failed/`，并在检查点中记录重试次数；
- 每篇文章处理后原子更新检查点；恢复运行时跳过已完成项并重试失败项；
- 迁移必须可重复执行，并在目标冲突时停止而不是覆盖。

### 性能

- 当前数据规模使用文件扫描和 `ArticleRepository` 即可；
- 只有基准测试证明磁盘扫描成为瓶颈后，才允许增加目录 mtime 缓存或内存倒排索引；
- 缓存必须隐藏在 Repository 接口后，不得污染 MCP 协议层或流水线；
- 网络请求必须设置超时，批量规模必须受 `--limit` 控制。

### 数据与合规

- 每篇文章必须保留 `source` 和 `source_url` 以支持来源追溯；
- 采集器必须尊重数据源服务条款、访问限制和合理请求频率；
- 不保存与知识文章无关的个人敏感信息；
- 删除、批量覆盖或不可逆迁移正式文章前必须取得用户明确确认；
- MCP 统计只读取 `knowledge/articles/`，不得把 fixtures、raw 或 failed 数据计入正式知识库。

## 7. 决策优先级

遇到冲突时按以下顺序决策：

1. 系统、平台策略以及安全、合规和数据完整性约束；
2. 用户在当前任务中的明确要求；
3. 更深目录中的补充 `AGENTS.md`；
4. 本 `AGENTS.md`；
5. README、代码注释和既有实现。

如果需求会突破上述边界，Agent 必须先说明影响和替代方案，再请求用户确认，不得静默扩大范围。
