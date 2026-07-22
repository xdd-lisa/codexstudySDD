"""Pipeline orchestration with per-item isolation and checkpoint recovery."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

# Support the documented direct invocation: ``python pipeline/pipeline.py``.
if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))
    sys.path.insert(0, str(project_root / "src"))

from knowledge_base.repository import ArticleRepository  # noqa: I001
from knowledge_base.schema import (
    ARTICLE_SCHEMA_VERSION,
    assert_valid_article,
    normalize_timestamp,
)

from pipeline.collector import (
    RSS_CONFIG_PATH,
    collect_github,
    collect_rss,
    load_rss_sources,
    short_hash,
    utc_now,
)
from pipeline.model_client import LLMProvider, LLMResponse, chat_with_retry, create_provider
from pipeline.storage import (
    load_checkpoint,
    next_raw_path,
    record_failure,
    save_checkpoint,
    save_raw,
)

LOGGER = logging.getLogger("knowledge_pipeline")
SUPPORTED_SOURCES = ("github", "rss")
HTTP_TIMEOUT_SECONDS = 30.0
ANALYSIS_FORMAT_ATTEMPTS = 3
RawItem = dict[str, Any]
Article = dict[str, Any]


def _analyze_item(
    provider: LLMProvider, item: Mapping[str, Any]
) -> tuple[LLMResponse, dict[str, Any]]:
    """Analyze one item and retry only malformed model output."""

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
    for attempt in range(1, ANALYSIS_FORMAT_ATTEMPTS + 1):
        response = chat_with_retry(provider, messages, temperature=0.2, max_tokens=400)
        try:
            return response, _parse_llm_analysis(response.content)
        except ValueError as error:
            if attempt == ANALYSIS_FORMAT_ATTEMPTS:
                raise ValueError(
                    f"LLM analysis for {item['title']!r} remained invalid after "
                    f"{ANALYSIS_FORMAT_ATTEMPTS} attempts: {error}"
                ) from error
            LOGGER.warning("Invalid analysis for %s; retrying format", item["title"])
            messages.extend(
                [
                    {"role": "assistant", "content": response.content},
                    {
                        "role": "user",
                        "content": f"Invalid response: {error}. Return strict JSON only.",
                    },
                ]
            )
    raise AssertionError("unreachable")


def normalize_article(
    item: Mapping[str, Any], response: LLMResponse, analysis: Mapping[str, Any]
) -> Article:
    """Build the canonical article representation."""

    source_url = normalize_url(clean_text(item.get("source_url")))
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
    assert_valid_article(article)
    return article


def run_pipeline(
    sources: Sequence[str],
    limit: int,
    dry_run: bool = False,
    *,
    resume: bool = True,
    rss_config: Path = RSS_CONFIG_PATH,
) -> int:
    """Collect and process items while isolating individual failures."""

    collected: list[RawItem] = []
    collection_failures: list[dict[str, Any]] = []
    source_limits = distribute_limit(sources, limit)
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
        if "github" in sources:
            try:
                collected.extend(collect_github(client, source_limits["github"]))
            except (httpx.HTTPError, ValueError) as error:
                collection_failures.append(
                    {"stage": "collect", "source": "github", "error": str(error)}
                )
        if "rss" in sources:
            try:
                rss_items, rss_failures = collect_rss(
                    client, source_limits["rss"], load_rss_sources(rss_config)
                )
                collected.extend(rss_items)
                collection_failures.extend(rss_failures)
            except ValueError as error:
                collection_failures.append(
                    {"stage": "collect", "source": "rss-config", "error": str(error)}
                )

    save_raw(collected, dry_run=dry_run)
    if not dry_run:
        for failure in collection_failures:
            record_failure(failure)

    checkpoint = (
        load_checkpoint()
        if resume and not dry_run
        else {"version": 1, "completed": {}, "failed": {}}
    )
    completed = checkpoint.setdefault("completed", {})
    failed = checkpoint.setdefault("failed", {})
    pending = [item for item in collected if not resume or item.get("external_id") not in completed]
    if not pending:
        LOGGER.info("Nothing pending; %d collected items already completed", len(collected))
        return 0

    provider = create_provider()
    repository = ArticleRepository()
    existing = repository.load_all()
    seen_urls = {normalize_url(clean_text(item.get("source_url"))) for item in existing}
    seen_titles = {clean_text(item.get("title")).casefold() for item in existing}
    succeeded = 0

    for item in pending:
        external_id = clean_text(item.get("external_id")) or short_hash(
            clean_text(item.get("source_url"))
        )
        try:
            response, analysis = _analyze_item(provider, item)
            article = normalize_article(item, response, analysis)
            url_key = normalize_url(article["source_url"])
            title_key = article["title"].casefold()
            if url_key in seen_urls or title_key in seen_titles:
                completed[external_id] = article["id"]
            else:
                if not dry_run:
                    repository.save(article)
                seen_urls.add(url_key)
                seen_titles.add(title_key)
                completed[external_id] = article["id"]
                succeeded += 1
            failed.pop(external_id, None)
        except (httpx.HTTPError, OSError, ValueError, RuntimeError) as error:
            failure = {
                "stage": "process",
                "external_id": external_id,
                "item": dict(item),
                "error": str(error),
            }
            previous = failed.get(external_id, {})
            failure["attempts"] = (
                int(previous.get("attempts", 0)) + 1 if isinstance(previous, Mapping) else 1
            )
            failed[external_id] = failure
            if not dry_run:
                record_failure(failure)
            LOGGER.error("Isolated failed item %s: %s", external_id, error)
        if not dry_run:
            save_checkpoint(checkpoint)

    LOGGER.info("Pipeline complete: %d saved, %d failed", succeeded, len(failed))
    return 0 if succeeded or not pending else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect and analyze AI knowledge articles.")
    parser.add_argument("--sources", type=parse_sources, default=list(SUPPORTED_SOURCES))
    parser.add_argument("--limit", type=positive_int, default=20)
    parser.add_argument("--rss-config", type=Path, default=RSS_CONFIG_PATH)
    parser.add_argument(
        "--no-resume", action="store_true", help="ignore the checkpoint for this run"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return run_pipeline(
            args.sources,
            args.limit,
            args.dry_run,
            resume=not args.no_resume,
            rss_config=args.rss_config,
        )
    except (OSError, ValueError, RuntimeError) as error:
        LOGGER.error("Pipeline failed: %s", error)
        return 1


def _parse_llm_analysis(content: str) -> dict[str, Any]:
    text = content.strip().lstrip("\ufeff")
    candidates = [text]
    if fenced := re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL):
        candidates.append(fenced.group(1).strip())
    if (start := text.find("{")) >= 0:
        try:
            extracted, _ = json.JSONDecoder().raw_decode(text, start)
            candidates.append(json.dumps(extracted, ensure_ascii=False))
        except json.JSONDecodeError:
            pass
    parse_error: json.JSONDecodeError | None = None
    payload: Any = None
    for candidate in dict.fromkeys(candidates):
        try:
            payload = json.loads(candidate)
            break
        except json.JSONDecodeError as error:
            parse_error = error
    else:
        if parse_error is None:
            raise ValueError("LLM analysis is empty")
        raise ValueError(
            f"LLM analysis is not valid JSON (line {parse_error.lineno}, column {parse_error.colno}: {parse_error.msg})"
        ) from parse_error
    if not isinstance(payload, Mapping):
        raise ValueError("LLM analysis must be an object")
    summary = clean_text(payload.get("summary"))
    score = payload.get("score")
    tags = normalize_tags(payload.get("tags", []))
    if len(summary) < 20:
        raise ValueError("summary must contain at least 20 characters")
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not 0 <= float(score) <= 10
    ):
        raise ValueError("score must be between 0 and 10")
    if not tags:
        raise ValueError("tags must be a non-empty list")
    return {"summary": summary, "score": round(float(score), 2), "tags": tags}


def normalize_tags(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    tags: list[str] = []
    for value in values:
        tag = re.sub(r"[^a-z0-9+#.-]+", "-", clean_text(value).lower()).strip("-")
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:5]


def normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/") or "/",
            parsed.query,
            "",
        )
    )


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""


def distribute_limit(sources: Sequence[str], limit: int) -> dict[str, int]:
    base, remainder = divmod(limit, len(sources))
    return {source: base + (index < remainder) for index, source in enumerate(sources)}


def parse_sources(value: str) -> list[str]:
    sources = list(dict.fromkeys(part.strip().lower() for part in value.split(",") if part.strip()))
    unsupported = [source for source in sources if source not in SUPPORTED_SOURCES]
    if not sources or unsupported:
        raise argparse.ArgumentTypeError("sources must be a comma-separated subset of github,rss")
    return sources


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("limit must be an integer") from error
    if number <= 0:
        raise argparse.ArgumentTypeError("limit must be greater than zero")
    return number


# Backward-compatible names used by existing callers/tests.
_next_raw_path = next_raw_path


if __name__ == "__main__":
    raise SystemExit(main())
