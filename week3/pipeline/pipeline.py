"""AI 知识文章流水线编排。

本模块串联采集、LLM 分析、文章规范化、去重、持久化、失败隔离与检查点恢复。
具体的采集协议、模型 HTTP 调用、文件写入和 Article Schema 分别由对应模块负责。
"""

# 延迟求值类型标注，避免运行时解析尚未加载的类型。
from __future__ import annotations

# 标准库分别承担 CLI、JSON、日志、文本清理、导入路径、类型与 URL 规范化。
import argparse
import json
import logging
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

# httpx 同时用于采集 HTTP 会话及精确捕获网络异常。
import httpx

# 无论通过模块还是脚本启动，都优先使用当前项目的共享领域包，避免误用其他
# editable install 指向的同名 knowledge_base。
project_root = Path(__file__).resolve().parents[1]
src_root = project_root / "src"
if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))
# 直接运行 ``python pipeline/pipeline.py`` 时 Python 还不会自动加入项目根。
if __package__ in {None, ""} and str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 正式文章必须通过共享 Repository 保存，不能在编排层自行拼接文件名。
from knowledge_base.repository import ArticleRepository  # noqa: E402, I001
# Schema 版本、校验器和时间规范化均来自唯一领域事实源。
from knowledge_base.schema import (  # noqa: E402
    ARTICLE_SCHEMA_VERSION,
    assert_valid_article,
    normalize_timestamp,
)

# collector 模块封装来源协议；本模块只负责选择来源和组合结果。
from pipeline.collector import (  # noqa: E402
    RSS_CONFIG_PATH,
    collect_container_ai,
    collect_github,
    collect_github_trending,
    collect_rss,
    load_rss_sources,
    short_hash,
    utc_now,
)
# model_client 对业务暴露统一 Provider 接口、重试和成本跟踪。
from pipeline.model_client import (  # noqa: E402
    LLMProvider,
    LLMResponse,
    chat_with_retry,
    create_provider,
    tracker,
)
# storage 模块负责 raw、failed、checkpoint 的安全持久化。
from pipeline.storage import (  # noqa: E402
    load_checkpoint,
    next_raw_path,
    record_failure,
    save_checkpoint,
    save_raw,
    save_weekly_container_ai,
)

# 使用固定 logger 名称，便于命令行统一过滤流水线日志。
LOGGER = logging.getLogger("knowledge_pipeline")
# 命令行只接受实现并测试过的来源。
SUPPORTED_SOURCES = ("github", "github-trending", "container-ai", "rss")
DEFAULT_SOURCES = ("github", "rss")
# 采集阶段整个 HTTP 操作的超时时间，防止单个来源无限阻塞。
HTTP_TIMEOUT_SECONDS = 30.0
# 模型返回成功但格式错误时，最多进行三轮对话纠正。
ANALYSIS_FORMAT_ATTEMPTS = 3
# 原始条目尚未经过领域校验，因此保留动态字典类型。
RawItem = dict[str, Any]
# Article 在运行时由共享 Schema 做严格校验。
Article = dict[str, Any]


def _analyze_item(
    provider: LLMProvider, item: Mapping[str, Any]
) -> tuple[LLMResponse, dict[str, Any]]:
    """分析一个采集条目，并仅针对格式错误纠正模型输出。"""

    # system 消息定义机器可校验的输出契约，减少自由文本带来的解析歧义。
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "Return only strict JSON with exactly summary, score, and tags. "
                "summary is concise Chinese with at least 20 characters; score "
                "is a number from 0 to 10; tags is 1-5 normalized lowercase "
                "English strings. Do not use Markdown."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Title: {item['title']}\nURL: {item['source_url']}\n"
                f"Source: {item['source']}\nContent: {item.get('content', '')}"
            ),
        },
    ]
    # 格式重试独立于 model_client 中的网络重试：这里处理的是成功响应中的坏内容。
    for attempt in range(1, ANALYSIS_FORMAT_ATTEMPTS + 1):
        # 所有尝试复用调用方传入的 provider，因此这里不会自动切换模型。
        response = chat_with_retry(provider, messages, temperature=0.2, max_tokens=400)
        try:
            # 只有解析和业务约束都通过时，才把响应交给下游。
            return response, _parse_llm_analysis(response.content)
        except ValueError as error:
            # 最后一轮仍失败时保留文章标题和根因，交由条目级失败隔离处理。
            if attempt == ANALYSIS_FORMAT_ATTEMPTS:
                raise ValueError(
                    f"LLM analysis for {item['title']!r} remained invalid after "
                    f"{ANALYSIS_FORMAT_ATTEMPTS} attempts: {error}"
                ) from error
            # 中间失败只记录摘要，不记录可能包含外部内容的完整模型响应。
            LOGGER.warning("Invalid analysis for %s; retrying format", item["title"])
            # 把错误答案与精确校验原因加入上下文，引导同一模型自我修正。
            messages.extend(
                [
                    {"role": "assistant", "content": response.content},
                    {
                        "role": "user",
                        "content": f"Invalid response: {error}. Return strict JSON only.",
                    },
                ]
            )
    # range 必然返回或抛错；该语句只为类型检查器表达不可达状态。
    raise AssertionError("unreachable")


def normalize_article(
    item: Mapping[str, Any], response: LLMResponse, analysis: Mapping[str, Any]
) -> Article:
    """把采集数据与模型分析组合为通过共享 Schema 的标准文章。"""

    # 先规范 URL，确保 ID、去重键和最终保存值基于同一表示。
    source_url = normalize_url(clean_text(item.get("source_url")))
    # 显式构造全部 Schema 字段，避免原始输入中的额外字段泄漏到正式知识库。
    article: Article = {
        "schema_version": ARTICLE_SCHEMA_VERSION,
        "id": short_hash(source_url),
        "title": clean_text(item.get("title")),
        "source": clean_text(item.get("source")).lower(),
        "source_url": source_url,
        "published_at": normalize_timestamp(item.get("published_at")),
        "collected_at": normalize_timestamp(item.get("collected_at")) or utc_now(),
        "summary": clean_text(analysis.get("summary")),
        "score": round(float(analysis.get("score", 0)), 2),
        "tags": normalize_tags(analysis.get("tags", [])),
        "status": "draft",
        "analysis": {"provider": response.provider, "model": response.model},
    }
    # LLM 与外部来源均不可信；保存前必须通过领域层最终校验。
    assert_valid_article(article)
    # 校验通过后，调用方才可以执行去重和持久化。
    return article


def run_pipeline(
    sources: Sequence[str],
    limit: int,
    dry_run: bool = False,
    *,
    resume: bool = True,
    rss_config: Path = RSS_CONFIG_PATH,
) -> int:
    """采集并处理文章，同时隔离来源级和条目级失败。"""

    # 聚合所有成功来源的条目，后续统一保存 raw 和进入处理阶段。
    collected: list[RawItem] = []
    # 来源失败单独收集，使一个来源异常不阻断其他来源。
    collection_failures: list[dict[str, Any]] = []
    # 总额度按来源尽量均匀分配，避免每个来源都使用完整 limit。
    source_limits = distribute_limit(sources, limit)
    # GitHub 与 RSS 复用一个有超时、支持重定向的 HTTP 客户端。
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
        # 仅在命令行选择 GitHub 时发起对应采集。
        if "github" in sources:
            try:
                # 成功结果直接追加到统一原始条目集合。
                collected.extend(collect_github(client, source_limits["github"]))
            except (httpx.HTTPError, ValueError) as error:
                # 网络或响应数据错误被降级为来源级失败。
                collection_failures.append(
                    {"stage": "collect", "source": "github", "error": str(error)}
                )
        # Trending 使用周榜页面证据和仓库 API 元数据，不与通用 GitHub Search 混用。
        if "github-trending" in sources:
            try:
                collected.extend(
                    collect_github_trending(client, source_limits["github-trending"])
                )
            except (httpx.HTTPError, ValueError) as error:
                collection_failures.append(
                    {
                        "stage": "collect",
                        "source": "github-trending",
                        "error": str(error),
                    }
                )
        # Container AI 成功保存专用周快照后，才允许条目进入公共 raw 和分析流程。
        if "container-ai" in sources:
            try:
                container_items = collect_container_ai(
                    client, source_limits["container-ai"]
                )
                snapshot_time = (
                    datetime.fromisoformat(container_items[0]["collected_at"])
                    if container_items
                    else None
                )
                save_weekly_container_ai(
                    container_items,
                    now=snapshot_time,
                    dry_run=dry_run,
                )
                collected.extend(container_items)
            except (httpx.HTTPError, OSError, ValueError) as error:
                collection_failures.append(
                    {
                        "stage": "collect",
                        "source": "container-ai",
                        "error": str(error),
                    }
                )
        # RSS 与 GitHub 相互隔离，即使前者或后者失败仍继续执行。
        if "rss" in sources:
            try:
                # RSS 采集器可返回部分成功条目以及单个 feed 的失败列表。
                rss_items, rss_failures = collect_rss(
                    client, source_limits["rss"], load_rss_sources(rss_config)
                )
                # 合并成功条目，保持后续处理逻辑与来源无关。
                collected.extend(rss_items)
                # 合并 feed 级失败，稍后统一持久化。
                collection_failures.extend(rss_failures)
            except ValueError as error:
                # RSS 配置整体无效时记录配置级失败。
                collection_failures.append(
                    {"stage": "collect", "source": "rss-config", "error": str(error)}
                )

    # 原始批次用于审计和问题复现；dry-run 时由 storage 跳过实际写入。
    save_raw(collected, dry_run=dry_run)
    # dry-run 禁止持久化失败记录，以免预览污染运行状态。
    if not dry_run:
        # 每个来源失败独立落盘，保留尽可能具体的失败范围。
        for failure in collection_failures:
            record_failure(failure)

    # 正常恢复运行读取历史检查点；no-resume 和 dry-run 使用临时空状态。
    checkpoint = (
        load_checkpoint()
        if resume and not dry_run
        else {"version": 1, "completed": {}, "failed": {}}
    )
    # setdefault 兼容缺少字段的旧检查点，并取得可原地更新的完成映射。
    completed = checkpoint.setdefault("completed", {})
    # failed 保存未完成条目的最近错误和累计尝试次数。
    failed = checkpoint.setdefault("failed", {})
    # 恢复模式跳过已完成 external_id；禁用恢复则重新处理全部采集结果。
    pending = [item for item in collected if not resume or item.get("external_id") not in completed]
    # 没有待处理数据时无需创建 LLM provider，也不会产生模型费用。
    if not pending:
        LOGGER.info("Nothing pending; %d collected items already completed", len(collected))
        return 0

    # 一次运行只创建一个 provider，所有条目和格式重试使用同一个模型。
    provider = create_provider()
    # Repository 统一正式文章的路径、命名和原子保存规则。
    repository = ArticleRepository()
    # 加载历史正式文章，为跨批次去重建立初始集合。
    existing = repository.load_all()
    # URL 先规范化，避免大小写、末尾斜杠等表示差异绕过去重。
    seen_urls = {normalize_url(clean_text(item.get("source_url"))) for item in existing}
    # casefold 比 lower 更适合构造面向 Unicode 的无大小写标题键。
    seen_titles = {clean_text(item.get("title")).casefold() for item in existing}
    # 只统计本次真正新增并保存的文章，不包含重复项。
    succeeded = 0

    # 顺序处理便于逐条更新检查点，并使失败与费用行为保持可预测。
    for item in pending:
        # external_id 是恢复身份；缺失时用来源 URL 哈希提供稳定后备值。
        external_id = clean_text(item.get("external_id")) or short_hash(
            clean_text(item.get("source_url"))
        )
        try:
            # LLM 分析同时返回原始响应元数据和经过约束校验的业务字段。
            response, analysis = _analyze_item(provider, item)
            # 构造标准 Article，并在函数内部执行共享 Schema 校验。
            article = normalize_article(item, response, analysis)
            # 使用与历史集合一致的 URL 规范化规则生成去重键。
            url_key = normalize_url(article["source_url"])
            # 标题采用无大小写键，覆盖同标题的常见重复来源。
            title_key = article["title"].casefold()
            # URL 或标题任一命中即认为已经收录。
            if url_key in seen_urls or title_key in seen_titles:
                # 重复项也标记完成，避免恢复运行反复支付分析成本。
                completed[external_id] = article["id"]
            else:
                # dry-run 执行完整分析与校验，但禁止写入正式知识库。
                if not dry_run:
                    repository.save(article)
                # 立即更新内存集合，使同一批次后续条目也能去重。
                seen_urls.add(url_key)
                seen_titles.add(title_key)
                # 保存 external_id 到 article ID 的映射，供恢复阶段跳过。
                completed[external_id] = article["id"]
                # 计数语义是“本批新增”，dry-run 中表示本可新增的数量。
                succeeded += 1
            # 条目成功或判重后，清除它之前遗留的失败状态。
            failed.pop(external_id, None)
        except (httpx.HTTPError, OSError, ValueError, RuntimeError) as error:
            # 只捕获流程预期的网络、存储、校验和运行时错误。
            failure = {
                "stage": "process",
                "external_id": external_id,
                "item": dict(item),
                "error": str(error),
            }
            # 读取旧失败记录以跨恢复运行累计尝试次数。
            previous = failed.get(external_id, {})
            # 非 Mapping 的损坏旧值按首次失败处理，避免二次异常中断批次。
            failure["attempts"] = (
                int(previous.get("attempts", 0)) + 1 if isinstance(previous, Mapping) else 1
            )
            # 内存检查点始终更新，即便 dry-run 最终不会持久化。
            failed[external_id] = failure
            # 正常运行将详细失败另存，方便不读取检查点也能排障。
            if not dry_run:
                record_failure(failure)
            # 单篇失败只记录并继续下一条，实现条目级故障隔离。
            LOGGER.error("Isolated failed item %s: %s", external_id, error)
        # 每篇结束后提交检查点，尽量缩小中断造成的重复工作窗口。
        if not dry_run:
            save_checkpoint(checkpoint)

    # 批次日志展示实际新增数与当前仍失败的条目数。
    LOGGER.info("Pipeline complete: %d saved, %d failed", succeeded, len(failed))
    # tracker 按本次单一 provider 汇总 token 与估算费用。
    tracker.report(provider=getattr(provider, "provider_name", "deepseek"))
    # 至少成功一篇或没有待处理项视为成功；全量失败返回非零。
    return 0 if succeeded or not pending else 1


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""

    # description 会展示在 ``--help`` 的开头。
    parser = argparse.ArgumentParser(description="Collect and analyze AI knowledge articles.")
    # parse_sources 同时负责逗号拆分、去重和白名单校验。
    parser.add_argument("--sources", type=parse_sources, default=list(DEFAULT_SOURCES))
    # positive_int 保证采集总额度始终大于零。
    parser.add_argument("--limit", type=positive_int, default=20)
    # Path 允许调用方使用自定义 RSS YAML 配置。
    parser.add_argument("--rss-config", type=Path, default=RSS_CONFIG_PATH)
    # CLI 使用负向开关，运行函数内部仍接收语义清晰的 resume 布尔值。
    parser.add_argument(
        "--no-resume", action="store_true", help="ignore the checkpoint for this run"
    )
    # dry-run 禁止 raw、failure、checkpoint 和正式文章的持久化。
    parser.add_argument("--dry-run", action="store_true")
    # verbose 只改变日志级别，不改变业务行为。
    parser.add_argument("--verbose", action="store_true")
    # 返回解析器便于 main 使用，也方便测试单独验证 CLI。
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行、配置日志并返回进程退出码。"""

    # argv=None 时 argparse 自动读取 sys.argv，测试可显式传入参数序列。
    args = build_parser().parse_args(argv)
    # 全局日志配置只在 CLI 边界执行，导入模块不会产生副作用。
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        # 把命令行名称转换成 run_pipeline 所需的正向参数语义。
        return run_pipeline(
            args.sources,
            args.limit,
            args.dry_run,
            resume=not args.no_resume,
            rss_config=args.rss_config,
        )
    except (OSError, ValueError, RuntimeError) as error:
        # 批次外层的配置/初始化错误在 CLI 边界转换为退出码 1。
        LOGGER.error("Pipeline failed: %s", error)
        return 1


def _parse_llm_analysis(content: str) -> dict[str, Any]:
    """从模型文本中提取并验证 summary、score 和 tags。"""

    # 去除首尾空白及部分模型可能输出的 UTF-8 BOM。
    text = content.strip().lstrip("\ufeff")
    # 优先尝试把完整响应直接当作 JSON，保持正常路径最简单。
    candidates = [text]
    # 兼容模型未遵守指令而返回 Markdown JSON 代码块的情况。
    if fenced := re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL):
        candidates.append(fenced.group(1).strip())
    # 兼容 JSON 前后夹带解释文字：从第一个左花括号开始解码一个对象。
    if (start := text.find("{")) >= 0:
        try:
            # raw_decode 返回首个 JSON 值及结束位置；结束位置在此无需使用。
            extracted, _ = json.JSONDecoder().raw_decode(text, start)
            # 重新序列化为候选字符串，复用下方统一 json.loads 路径。
            candidates.append(json.dumps(extracted, ensure_ascii=False))
        except json.JSONDecodeError:
            # 提取失败不立即终止，完整文本或 fenced 候选仍可能有效。
            pass
    # 保存最后一次语法错误，为最终报错提供行列信息。
    parse_error: json.JSONDecodeError | None = None
    # payload 在成功解析前保持动态类型，随后再验证必须是对象。
    payload: Any = None
    # dict.fromkeys 在保持优先顺序的同时去掉重复候选。
    for candidate in dict.fromkeys(candidates):
        try:
            # 首个合法 JSON 候选胜出，避免后续宽松候选覆盖严格结果。
            payload = json.loads(candidate)
            break
        except json.JSONDecodeError as error:
            # 暂存错误并继续尝试其他兼容格式。
            parse_error = error
    else:
        # 没有语法错误通常意味着候选为空，返回更直观的业务错误。
        if parse_error is None:
            raise ValueError("LLM analysis is empty")
        # 将 JSON 解析细节包装为稳定的流水线 ValueError。
        raise ValueError(
            f"LLM analysis is not valid JSON (line {parse_error.lineno}, column {parse_error.colno}: {parse_error.msg})"
        ) from parse_error
    # 数组、字符串等合法 JSON 仍不符合分析对象契约。
    if not isinstance(payload, Mapping):
        raise ValueError("LLM analysis must be an object")
    # 摘要统一压缩空白，避免只靠空白满足长度约束。
    summary = clean_text(payload.get("summary"))
    # 分数保留原始动态类型，以便明确拒绝布尔值和字符串数字。
    score = payload.get("score")
    # 标签在校验非空前完成字符清洗、去重和数量限制。
    tags = normalize_tags(payload.get("tags", []))
    # 较短文本不足以作为正式文章摘要。
    if len(summary) < 20:
        raise ValueError("summary must contain at least 20 characters")
    # bool 是 int 的子类，因此必须在数值类型检查前显式排除。
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not 0 <= float(score) <= 10
    ):
        raise ValueError("score must be between 0 and 10")
    # Article Schema 要求至少一个规范标签。
    if not tags:
        raise ValueError("tags must be a non-empty list")
    # 分数统一保留两位小数，返回下游所需的最小字段集合。
    return {"summary": summary, "score": round(float(score), 2), "tags": tags}


def normalize_tags(values: Any) -> list[str]:
    """清洗、去重标签并最多返回五个规范标签。"""

    # 拒绝字符串等可迭代对象，避免把字符误当成独立标签。
    if not isinstance(values, (list, tuple)):
        return []
    # 使用列表保持模型给出的标签优先顺序。
    tags: list[str] = []
    # 每个原始值独立清洗，非字符串值由 clean_text 安全转换。
    for value in values:
        # 仅保留小写字母数字及常用技术标签符号，其余片段折叠为连字符。
        tag = re.sub(r"[^a-z0-9+#.-]+", "-", clean_text(value).lower()).strip("-")
        # 丢弃空标签，并以首次出现为准去重。
        if tag and tag not in tags:
            tags.append(tag)
    # Article Schema 最多允许五个标签。
    return tags[:5]


def normalize_url(value: str) -> str:
    """生成适合稳定 ID 和去重的 URL 表示。"""

    # 拆分 URL 后分别规范各组成部分，避免对 query 做破坏性字符串替换。
    parsed = urlsplit(value.strip())
    # fragment 不影响服务端资源身份，因此最终明确丢弃。
    return urlunsplit(
        (
            # scheme 和 host 大小写不敏感，统一转小写。
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            # 非根路径移除末尾斜杠；空路径统一为根路径。
            parsed.path.rstrip("/") or "/",
            # query 可能影响资源内容，保留原样和顺序。
            parsed.query,
            # fragment 仅表示页面内位置，不参与文章身份。
            "",
        )
    )


def clean_text(value: Any) -> str:
    """把动态值转换为单行、空白规范化后的文本。"""

    # None 表示缺失；其他值转字符串后将连续空白压缩成单个空格。
    return re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""


def distribute_limit(sources: Sequence[str], limit: int) -> dict[str, int]:
    """把总采集额度尽量平均地分配给所选来源。"""

    # divmod 同时得到每个来源的基础额度和不能整除的余数。
    base, remainder = divmod(limit, len(sources))
    # 前 remainder 个来源各多分配一个，保证分配总和严格等于 limit。
    return {source: base + (index < remainder) for index, source in enumerate(sources)}


def parse_sources(value: str) -> list[str]:
    """解析逗号分隔来源，并校验其属于支持列表。"""

    # 拆分、去空白、转小写，并利用有序字典语义按首次出现去重。
    sources = list(dict.fromkeys(part.strip().lower() for part in value.split(",") if part.strip()))
    # 单独收集未知来源，使空输入与非法输入走同一参数错误。
    unsupported = [source for source in sources if source not in SUPPORTED_SOURCES]
    # argparse.ArgumentTypeError 会让 CLI 展示标准用法和可读错误。
    if not sources or unsupported:
        choices = ",".join(SUPPORTED_SOURCES)
        raise argparse.ArgumentTypeError(
            f"sources must be a comma-separated subset of {choices}"
        )
    # 保留用户指定的来源顺序，因为额度余数按顺序分配。
    return sources


def positive_int(value: str) -> int:
    """把命令行字符串解析为严格正整数。"""

    try:
        # 先使用 Python 整数语义解析，拒绝小数等非整数格式。
        number = int(value)
    except ValueError as error:
        # 转换为 argparse 专用异常，以获得一致的 CLI 错误输出。
        raise argparse.ArgumentTypeError("limit must be an integer") from error
    # 零和负数无法形成有效采集额度。
    if number <= 0:
        raise argparse.ArgumentTypeError("limit must be greater than zero")
    # 返回类型已从字符串收窄为正整数。
    return number


# 保留历史私有名称，避免已有调用方和测试因函数迁移到 storage 而中断。
_next_raw_path = next_raw_path


# 仅直接执行本文件时启动 CLI；作为模块导入不会自动运行流水线。
if __name__ == "__main__":
    # SystemExit 将 main 返回的状态码传递给 shell。
    raise SystemExit(main())
