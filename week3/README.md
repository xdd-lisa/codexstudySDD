# AI Knowledge Pipeline

一个可恢复、单条失败隔离的 AI 文章采集流水线，同时通过本地 MCP 服务提供搜索、文章读取和统计能力。

## 安装与运行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
python -m pipeline --sources github,rss --limit 20
```

安装后也可以使用正式入口：

```bash
knowledge-pipeline --sources rss --limit 10
python .codex/mcp_servers/local_knowledge/main.py
```

RSS 默认读取 `pipeline/rss_sources.yaml` 中 `enabled: true` 的条目。可用 `--rss-config PATH` 指定另一份配置。

### GitHub Trending 周榜

GitHub Trending 是显式可选来源，不会改变默认的 `github,rss` 来源组合：

```bash
python -m pipeline --sources github-trending --limit 15
```

该来源读取 GitHub Trending 周榜展示的排名和 “stars this week”，再通过公开 GitHub API 补全仓库描述、总 Star、Fork、语言、Topics、License 和活跃时间。结果仅保留与 AI、LLM、Agent、模型训练、推理、评估或相关基础设施直接相关的项目，并排除 Awesome 清单和链接索引。

`popularity_raw` 与 `source_metrics.period_stars` 均表示同一周榜窗口的新增 Star；`popularity` 按本批最大值线性归一化到 0–100。分数相同时按规范化仓库 URL 排序。符合条件的项目不足 `--limit` 时会如实返回较少结果，不会补齐。

此来源不会把 GitHub Search 的总 Star 或更新时间称为 GitHub Trending 排名。未配置凭证时使用公开接口限额；可以导出 `GITHUB_TOKEN` 环境变量以提高仓库元数据请求限额。Token 只用于请求头，不会写入 raw、失败记录或日志。

### Container AI 周度项目

Container AI 是显式可选来源，默认来源仍保持 `github,rss`：

```bash
python -m pipeline --sources container-ai --limit 20
```

该来源通过 GitHub 公共 Search API 分别搜索最近七天创建和最近七天推送的候选仓库，合并后按采集时的当前总 Star 降序排列。这里的“更新”明确指 `pushed_at` 代码活动；总 Star 不表示最近七天新增 Star。

仓库必须同时具有容器领域证据（例如 Container、Kubernetes、Docker、OCI 或容器运行时）和 AI 生命周期证据（例如模型训练、推理、Serving、部署或 GPU 加速）。已归档仓库、Awesome/资源清单和以教程为主要目的的仓库会被排除。相同 `owner/repository` 只保留一次，Star 相同时按规范化 GitHub URL 排序。

该来源最多返回 `min(分配到的 --limit, 15)` 条；单独以默认全局额度运行时得到 Top 15，不足时不会补造数据。成功结果会先原子保存到：

```text
knowledge/weekly/YYMMDD-HHMM-cai.json
```

文件时间使用项目时区 `Asia/Shanghai`，文件名 stem 长度为 15，`.json` 不计入限制。同一分钟已有文件时运行会报告冲突，不覆盖旧文件；`--dry-run` 不写周文件。任一必需 Search 或周文件保存失败时，Container AI 批次不会部分进入分析，但其他来源仍会继续。可通过 `GITHUB_TOKEN` 提高 GitHub API 请求限额。

## 目录与职责

```text
pipeline/
├── collector.py     # GitHub/RSS 采集和 rss_sources.yaml 加载
├── storage.py       # 原始批次、失败目录和 checkpoint
├── pipeline.py      # 分析与流程编排
├── model_client.py  # LLM 提供商适配
└── migrate.py       # Article Schema 数据迁移

src/knowledge_base/
├── schema.py        # 唯一 Article Schema
└── repository.py    # 流水线与 MCP 共用的文章仓储

.codex/mcp_servers/local_knowledge/
├── main.py          # MCP 启动入口
└── server.py        # MCP 协议及 search/get/stats 工具

knowledge/
├── raw/             # 原始采集批次
├── weekly/          # Container AI 等主题周度原始快照
├── articles/        # 仅存放符合 Schema 的正式文章
├── failed/          # 按条目隔离的最近失败记录
└── checkpoint.json  # 已完成 ID 与失败重试状态

tests/fixtures/      # 测试文章，不参与生产知识库统计
```

## Article Schema v1

所有组件共同调用 `knowledge_base.schema`，不再重复定义规则。主要约束：

- `schema_version` 固定为 `1`；
- `id` 是来源 URL 的 16 位小写十六进制摘要；
- `score` 范围为 0–10；
- `published_at` 为 ISO 8601 或 `null`，`collected_at` 为 ISO 8601；
- `tags` 包含 1–5 个小写规范化标签；
- `analysis` 必须包含非空的 `provider` 和 `model`。

Hook 校验器、质量检查、流水线写入和 MCP 读取均复用该契约。

### 正式文章文件命名

`knowledge/articles/` 只使用以下格式：

```text
{source}-{short-title}-{id前8位}.json
```

- 全部使用小写 ASCII；非字母数字统一为单个 `-`；
- `source` 最长 12 个字符；`short-title` 最长 32 个字符；
- ID 取正文中完整 16 位 ID 的前 8 位，用于避免同名冲突；
- 文件名最长 63 个字符，完整标题和完整 ID 始终保存在 JSON 正文中。

示例：`github-leon-ai-leon-55f45778.json`。

## 失败隔离与恢复

采集源失败不会阻断其他来源；单篇分析或存储失败不会阻断后续文章。失败详情写入 `knowledge/failed/`，处理状态在每篇文章后原子写入 `knowledge/checkpoint.json`。再次运行时会跳过已完成项目并重试失败项目；使用 `--no-resume` 可忽略检查点。

## 验证

```bash
python -m unittest discover -s tests -v
python .codex/hooks/scripts/validate_json.py 'knowledge/articles/*.json'
python .codex/hooks/scripts/check_quality.py knowledge/articles/*.json
```

## MCP 扩展路线

当前 `ArticleRepository` 每次从磁盘读取，适合当前数据规模。数据量明显增长后，可在不修改 MCP 协议层的前提下，为仓储实现增加目录 mtime 缓存或内存倒排索引；在有基准数据证明磁盘扫描成为瓶颈前不提前引入该复杂度。
