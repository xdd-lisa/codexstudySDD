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
