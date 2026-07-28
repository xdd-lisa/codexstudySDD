# AI 知识文章流水线设计说明

## 1. 目标与边界

`pipeline/pipeline.py` 是知识文章流水线的编排层。它负责把采集、LLM 分析、领域校验、去重、持久化、失败隔离和检查点恢复串成一次可执行任务，但不在本模块中重复实现这些能力。

设计目标：

- 同时支持 GitHub 与 RSS 两类来源；
- 将不同来源的数据规范化为统一 Article Schema；
- 一个来源失败时继续采集其他来源；
- 一篇文章失败时继续处理后续文章；
- 中断后能够跳过已完成条目并重试失败条目；
- 正式文章写入前必须通过共享 Schema 校验；
- 保存 LLM provider 和 model，保证分析结果可追踪；
- 用 `--limit` 限制单批网络请求和 LLM 调用规模。

当前边界：

- 流水线只负责编排，不定义采集协议、文件原子写入规则、Article Schema 或 LLM HTTP 协议；
- 当前一次运行只创建一个 LLM provider，所有待处理文章使用同一个模型；
- 当前使用文件仓储，不包含数据库、任务队列、并发执行和自动模型路由；
- MCP 服务只消费正式知识库，不参与流水线写入。

## 2. 分层与依赖

```text
命令行参数
    │
    ▼
pipeline.py（流程编排）
    ├── collector.py（GitHub/RSS 采集）
    ├── model_client.py（模型配置、请求、重试、费用统计）
    ├── storage.py（raw、failed、checkpoint）
    └── knowledge_base
        ├── schema.py（Article Schema 唯一事实源）
        └── repository.py（正式文章仓储与命名）
```

依赖方向保持单向：编排层调用能力层和领域层；能力层不反向依赖编排层。这样可以独立测试采集器、模型客户端、存储以及文章领域规则。

## 3. 主流程

`run_pipeline()` 按以下阶段执行：

1. 根据来源数量分配本批 `limit`。
2. 复用一个带超时的 `httpx.Client`，分别采集 GitHub 和 RSS。
3. 隔离来源级异常，记录失败但继续其他来源。
4. 保存完整原始采集批次，保留可审计输入。
5. 加载检查点；`dry-run` 或禁用恢复时使用内存中的空检查点。
6. 根据 `completed` 集合筛出尚未完成的条目。
7. 创建一次 LLM provider，并加载正式文章建立 URL/标题去重集合。
8. 逐篇执行 LLM 分析、响应解析、Article 构造和 Schema 校验。
9. 对重复文章只标记完成；对新文章写入正式仓储。
10. 对单篇异常写入失败信息并累加尝试次数。
11. 每篇处理后保存检查点，缩小意外中断后的重复工作窗口。
12. 输出处理结果和本次 provider 的累计 token/费用报告。

流程图：

```text
采集 GitHub ─┐
             ├─> 保存 raw ─> 加载 checkpoint ─> 筛选 pending
采集 RSS ────┘                                  │
                                                ▼
                                      创建单一 LLM provider
                                                │
                                      ┌─────────┴─────────┐
                                      │ 逐篇处理 pending  │
                                      └─────────┬─────────┘
                                                │
                          LLM 分析 -> 解析 -> 规范化 -> Schema 校验
                                                │
                              ┌─────────────────┴─────────────────┐
                              ▼                                   ▼
                        重复：仅标记完成                      新文章：保存
                              │                                   │
                              └─────────────────┬─────────────────┘
                                                ▼
                                          保存 checkpoint
```

## 4. 数据生命周期

### 4.1 RawItem

采集器返回字典形式的 `RawItem`。编排层主要依赖：

- `external_id`：检查点身份；
- `title`、`source_url`、`source`、`content`：LLM 输入；
- `published_at`、`collected_at`：Article 时间字段。

若 `external_id` 缺失，流程用来源 URL 的短哈希作为本次处理身份。

### 4.2 LLM 分析结果

模型被要求只返回以下 JSON：

```json
{
  "summary": "至少 20 个字符的中文摘要",
  "score": 8.5,
  "tags": ["python", "llm"]
}
```

解析器兼容三种常见输出：

- 纯 JSON；
- Markdown JSON 代码块；
- JSON 前后带少量解释文本。

兼容只发生在语法提取阶段。提取后仍严格检查对象类型、摘要长度、分数范围和非空标签，避免把不可靠的模型输出直接写入知识库。

### 4.3 Article

`normalize_article()` 将 RawItem、LLM 内容和响应元数据合并为规范 Article：

- URL 被规范化并用于生成稳定 ID；
- 文本被压缩空白；
- tag 转为小写、过滤字符、去重并最多保留 5 个；
- `status` 初始为 `draft`；
- `analysis` 保存实际 provider/model；
- 最后调用共享 `assert_valid_article()`，通过后才允许保存。

## 5. 失败模型

系统有三层失败处理：

### 5.1 来源级隔离

GitHub 请求失败不会阻止 RSS；RSS 配置或解析失败不会撤销已得到的 GitHub 数据。来源失败以 `stage=collect` 保存。

### 5.2 模型请求重试

`model_client.chat_with_retry()` 处理网络错误、HTTP 429 和 5xx，并执行有限次数的指数退避。非重试型 HTTP 错误直接上抛。

### 5.3 模型格式纠正

模型请求成功但内容不符合分析契约时，`_analyze_item()` 把错误原因和上次回答加入对话，再要求同一个模型修正。该过程最多执行 `ANALYSIS_FORMAT_ATTEMPTS` 次。

注意：格式纠正不是模型切换。当前不会因复杂度、来源或失败自动改用其他模型。

### 5.4 条目级隔离

单篇分析、校验或保存失败后：

- 构造 `stage=process` 的失败记录；
- 保存原始 item 和错误文本；
- 根据旧检查点累加 `attempts`；
- 不把该 external ID 加入 `completed`；
- 继续处理下一篇。

## 6. 检查点与恢复

检查点包含：

```json
{
  "version": 1,
  "completed": {
    "external-id": "article-id"
  },
  "failed": {
    "external-id": {
      "attempts": 1
    }
  }
}
```

恢复语义：

- 默认加载检查点并跳过 `completed` 中的条目；
- 失败条目不在 `completed` 中，因此下次运行会再次处理；
- 成功后从 `failed` 删除旧失败状态；
- `--no-resume` 忽略已有检查点；
- `--dry-run` 不读取持久化检查点，也不写正式文章、失败文件或检查点。

每篇结束后保存检查点，而非整个批次结束后保存。这增加少量 I/O，但显著降低中断后的重复 LLM 成本。

## 7. 去重策略

正式文章加载后建立两个内存集合：

- 规范化后的 `source_url`；
- 清洗并 `casefold()` 后的标题。

任意一个命中即视为重复。命中后仍把 external ID 标记为完成，防止恢复运行反复分析同一重复条目。

这是批次内与历史数据共同去重：保存新文章后立即更新两个集合，因此同一批次后续条目也能命中。

## 8. Dry-run 语义

`--dry-run` 会执行真实采集和真实 LLM 分析，但跳过本地持久化副作用：

- `save_raw()` 由 `dry_run` 参数决定是否写入；
- 不写 collection/process failure；
- 不加载或保存持久化 checkpoint；
- 不保存正式 Article。

因此它不是“零成本预览”：仍可能产生网络请求和 LLM token 费用。

## 9. 命令行设计

- `--sources github,rss`：选择来源，去重并拒绝未知值；
- `--limit N`：正整数，总额度平均分配给所选来源；
- `--rss-config PATH`：替换默认 RSS YAML；
- `--no-resume`：忽略已有检查点；
- `--dry-run`：禁止持久化写入；
- `--verbose`：启用 DEBUG 日志。

退出码语义：

- 有成功保存，或没有待处理条目：`0`；
- 有待处理条目但全部失败：`1`；
- 顶层配置、文件或运行时错误：记录日志后返回 `1`。

## 10. 模型选择设计

当前模型在 `run_pipeline()` 中通过 `create_provider()` 创建一次。选择来自环境变量：

```text
LLM_PROVIDER -> provider 专用配置
LLM_MODEL -> provider 专用模型变量 -> 内置默认模型
```

该 provider 被传给每次 `_analyze_item()`，所以所有文章和所有格式纠正重试均使用同一个模型。当前没有按步骤选模、按来源选模、按复杂度路由或失败降级。

如果未来需要多模型路由，建议：

1. 让 provider 工厂接受显式 model 参数，避免依赖进程级环境变量；
2. 引入独立 `ModelRouter`，不要在 `run_pipeline()` 中堆叠选择条件；
3. 路由结果仍以 `LLMProvider` 接口返回，保持 `_analyze_item()` 不感知厂商；
4. 每次响应继续保存实际 provider/model；
5. 成本统计改为按 provider/model 聚合；
6. 为路由决策、降级边界和不可用场景增加测试。

## 11. 关键设计取舍

- **顺序处理而非并发**：当前规模优先保证检查点、费用和错误行为简单可预测。
- **文件仓储而非数据库**：现有规模下更易检查、迁移和版本管理。
- **严格领域校验**：LLM 输出是不可信输入，兼容解析不能替代 Schema 校验。
- **URL 与标题双重去重**：覆盖同一内容不同 external ID、同标题不同采集入口的常见重复。
- **单条检查点提交**：用更多小文件写入换取更细粒度恢复。
- **统一 provider 实例**：配置和费用统计简单，但暂不支持任务级模型路由。

## 12. 扩展时的约束

- Article 字段变化必须先修改共享 Schema，并同步迁移、测试、Hook 和 MCP；
- 新来源应在 collector 层实现，编排层只负责调用和隔离；
- 新存储规则应进入 storage/repository，不应直接散落在主循环；
- 新模型供应商应实现 `LLMProvider`，不应让业务流程依赖厂商 SDK；
- 不得把 API key、Authorization header 或完整敏感响应写入日志和失败文件；
- 删除、覆盖或不可逆迁移正式文章前必须单独确认。
