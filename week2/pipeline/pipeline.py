"""Four-step automation pipeline for an AI knowledge base.

The pipeline collects content from GitHub and RSS, analyzes every item with an
LLM, normalizes and deduplicates the results, and saves one JSON file per
article.  Run this file directly so its sibling ``model_client.py`` is on the
Python import path::

    python pipeline/pipeline.py --sources github,rss --limit 20
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

import httpx

from model_client import LLMProvider, LLMResponse, create_provider, chat_with_retry

LOGGER = logging.getLogger("knowledge_pipeline")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "knowledge" / "raw"
ARTICLES_DIR = PROJECT_ROOT / "knowledge" / "articles"

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
GITHUB_QUERY = "topic:artificial-intelligence stars:>100"
DEFAULT_RSS_FEEDS = (
    "https://openai.com/news/rss.xml",
    "https://blog.google/technology/ai/rss/",
)
SUPPORTED_SOURCES = ("github", "rss")
HTTP_TIMEOUT_SECONDS = 30.0
USER_AGENT = "ai-knowledge-pipeline/1.0"
ANALYSIS_FORMAT_ATTEMPTS = 3

RawItem = dict[str, Any]
Article = dict[str, Any]


def collect_github(client: httpx.Client, limit: int) -> list[RawItem]:
    """Collect AI repositories from the GitHub Search API."""

    if limit <= 0:
        return []

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    LOGGER.info("Collecting up to %d repositories from GitHub", limit)
    response = client.get(
        GITHUB_SEARCH_URL,
        headers=headers,
        params={
            "q": GITHUB_QUERY,
            "sort": "updated",
            "order": "desc",
            "per_page": min(limit, 100),
        },
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items", []) if isinstance(payload, Mapping) else []
    if not isinstance(items, list):
        raise ValueError("GitHub Search API returned an invalid items field")

    collected_at = _utc_now()
    results: list[RawItem] = []
    for repository in items[:limit]:
        if not isinstance(repository, Mapping):
            continue
        name = _clean_text(repository.get("full_name"))
        url = _clean_text(repository.get("html_url"))
        if not name or not url:
            continue
        topics = repository.get("topics", [])
        if not isinstance(topics, list):
            topics = []
        description = _clean_text(repository.get("description"))
        language = _clean_text(repository.get("language"))
        details = [description] if description else []
        if language:
            details.append(f"Primary language: {language}.")
        details.append(
            f"GitHub stars: {_nonnegative_int(repository.get('stargazers_count'))}."
        )
        results.append(
            {
                "external_id": f"github:{name.lower()}",
                "title": name,
                "source": "github",
                "source_url": url,
                "published_at": _clean_text(repository.get("updated_at")),
                "collected_at": collected_at,
                "content": " ".join(details),
                "source_tags": [str(topic) for topic in topics if topic],
            }
        )
    LOGGER.info("Collected %d repositories from GitHub", len(results))
    return results


def collect_rss(
    client: httpx.Client,
    limit: int,
    feeds: Sequence[str] | None = None,
) -> list[RawItem]:
    """Collect entries from RSS/Atom feeds using a deliberately small parser."""

    if limit <= 0:
        return []

    feed_urls = tuple(feeds or _rss_feed_urls())
    results: list[RawItem] = []
    per_feed_limit = max(1, limit)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml"}

    for feed_url in feed_urls:
        if len(results) >= limit:
            break
        LOGGER.info("Collecting RSS feed %s", feed_url)
        try:
            response = client.get(feed_url, headers=headers)
            response.raise_for_status()
            entries = parse_rss(response.text, feed_url, per_feed_limit)
        except (httpx.HTTPError, ValueError) as error:
            LOGGER.warning("Skipping RSS feed %s: %s", feed_url, error)
            continue
        results.extend(entries[: limit - len(results)])

    LOGGER.info("Collected %d entries from RSS", len(results))
    return results


def parse_rss(xml_text: str, feed_url: str, limit: int) -> list[RawItem]:
    """Parse the common subset of RSS and Atom with regular expressions.

    This is intentionally not a general XML parser. It handles conventional
    ``item`` and ``entry`` blocks plus their common title, link, description,
    summary, content, publication date, and identifier fields.
    """

    matches = re.findall(
        r"<(item|entry)\b[^>]*>(.*?)</\1\s*>",
        xml_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    blocks = [block for _, block in matches]
    collected_at = _utc_now()
    entries: list[RawItem] = []
    for block in blocks[:limit]:
        title = _extract_xml_text(block, ("title",))
        link = _extract_link(block)
        if not title or not link:
            continue
        description = _extract_xml_text(
            block,
            ("description", "summary", "content:encoded", "content"),
        )
        published_at = _extract_xml_text(
            block,
            ("pubDate", "published", "updated", "dc:date"),
        )
        guid = _extract_xml_text(block, ("guid", "id"))
        stable_value = guid or link
        entries.append(
            {
                "external_id": f"rss:{_short_hash(stable_value)}",
                "title": title,
                "source": "rss",
                "source_url": link,
                "feed_url": feed_url,
                "published_at": published_at,
                "collected_at": collected_at,
                "content": description,
                "source_tags": [],
            }
        )
    return entries


def analyze_items(items: Sequence[RawItem]) -> list[Article]:
    """Analyze each collected item using the configured LLM provider."""

    if not items:
        return []

    provider = create_provider()
    analyzed: list[Article] = []
    total = len(items)
    for index, item in enumerate(items, start=1):
        LOGGER.info("Analyzing item %d/%d: %s", index, total, item["title"])
        response, analysis = _analyze_item(provider, item)
        analyzed.append(
            {
                **item,
                "summary": analysis["summary"],
                "score": analysis["score"],
                "tags": analysis["tags"],
                "analysis_provider": response.provider,
                "analysis_model": response.model,
            }
        )
    return analyzed


def _analyze_item(
    provider: LLMProvider,
    item: Mapping[str, Any],
) -> tuple[LLMResponse, dict[str, Any]]:
    """Analyze one item and retry when the model emits malformed JSON."""

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You analyze AI knowledge-base candidates. Return only strict "
                "JSON with exactly the keys summary, score, and tags. summary "
                "must be a concise Chinese summary; score must be a JSON number "
                "from 0 to 10; tags must be a JSON array containing 1-5 "
                "lowercase short English strings. Use double quotes for every "
                "key and string. Do not use comments or a Markdown code fence."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Title: {item['title']}\n"
                f"URL: {item['source_url']}\n"
                f"Source: {item['source']}\n"
                f"Content: {item.get('content', '')}"
            ),
        },
    ]

    for attempt in range(1, ANALYSIS_FORMAT_ATTEMPTS + 1):
        response = chat_with_retry(
            provider,
            messages,
            temperature=0.2,
            max_tokens=400,
        )
        try:
            return response, _parse_llm_analysis(response.content)
        except ValueError as error:
            LOGGER.debug(
                "Invalid LLM analysis for %s (format attempt %d/%d): %r",
                item["title"],
                attempt,
                ANALYSIS_FORMAT_ATTEMPTS,
                response.content,
            )
            if attempt == ANALYSIS_FORMAT_ATTEMPTS:
                raise ValueError(
                    f"LLM analysis for {item['title']!r} remained invalid after "
                    f"{ANALYSIS_FORMAT_ATTEMPTS} format attempts: {error}"
                ) from error
            LOGGER.warning(
                "LLM returned invalid analysis JSON for %s; requesting format "
                "correction (%d/%d)",
                item["title"],
                attempt + 1,
                ANALYSIS_FORMAT_ATTEMPTS,
            )
            messages.extend(
                [
                    {"role": "assistant", "content": response.content},
                    {
                        "role": "user",
                        "content": (
                            f"That response was invalid: {error}. Return the "
                            "same analysis again as one strict JSON object only."
                        ),
                    },
                ]
            )

    raise AssertionError("analysis format retry loop ended unexpectedly")


def organize_items(items: Sequence[Article]) -> list[Article]:
    """Deduplicate, normalize, and validate analyzed articles."""

    organized: list[Article] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()

    for item in items:
        article = _normalize_article(item)
        normalized_url = _normalize_url(article["source_url"])
        normalized_title = article["title"].casefold()
        if normalized_url in seen_urls or normalized_title in seen_titles:
            LOGGER.debug("Discarding duplicate article: %s", article["title"])
            continue
        _validate_article(article)
        seen_urls.add(normalized_url)
        seen_titles.add(normalized_title)
        organized.append(article)

    LOGGER.info(
        "Organized %d articles; removed %d duplicates",
        len(organized),
        len(items) - len(organized),
    )
    return organized


def save_raw(items: Sequence[RawItem], dry_run: bool = False) -> Path | None:
    """Save one collected batch under ``knowledge/raw``."""

    destination = _next_raw_path()
    if dry_run:
        LOGGER.info("Dry run: would save raw batch to %s", destination)
        return None
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(destination, list(items))
    LOGGER.info("Saved raw batch to %s", destination)
    return destination


def _next_raw_path(now: datetime | None = None) -> Path:
    """Return the next ``raw_YYYYMMDD_NNN.json`` path for a UTC day."""

    current = now or datetime.now(timezone.utc)
    date_part = current.astimezone(timezone.utc).strftime("%Y%m%d")
    pattern = re.compile(rf"^raw_{date_part}_(\d{{3,}})\.json$")
    sequences = [
        int(match.group(1))
        for path in RAW_DIR.glob(f"raw_{date_part}_*.json")
        if (match := pattern.fullmatch(path.name))
    ]
    sequence = max(sequences, default=0) + 1
    return RAW_DIR / f"raw_{date_part}_{sequence:03d}.json"


def save_articles(
    articles: Sequence[Article],
    dry_run: bool = False,
) -> list[Path]:
    """Save each normalized article as an independent JSON file."""

    destinations = [_article_path(article) for article in articles]
    if dry_run:
        LOGGER.info("Dry run: would save %d article files", len(destinations))
        for destination in destinations:
            LOGGER.debug("Dry run article path: %s", destination)
        return []

    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    for article, destination in zip(articles, destinations):
        _write_json(destination, article)
    LOGGER.info("Saved %d articles to %s", len(destinations), ARTICLES_DIR)
    return destinations


def run_pipeline(sources: Sequence[str], limit: int, dry_run: bool) -> int:
    """Execute collect, analyze, organize, and save in order."""

    LOGGER.info("Step 1/4 Collect")
    collected: list[RawItem] = []
    source_limits = _distribute_limit(sources, limit)
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
        if "github" in sources:
            collected.extend(collect_github(client, source_limits["github"]))
        if "rss" in sources:
            collected.extend(collect_rss(client, source_limits["rss"]))
    save_raw(collected, dry_run=dry_run)
    if not collected:
        LOGGER.warning("No content was collected; stopping the pipeline")
        return 0

    LOGGER.info("Step 2/4 Analyze")
    analyzed = analyze_items(collected)

    LOGGER.info("Step 3/4 Organize")
    organized = organize_items(analyzed)

    LOGGER.info("Step 4/4 Save")
    save_articles(organized, dry_run=dry_run)
    LOGGER.info("Pipeline complete: %d articles", len(organized))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""

    parser = argparse.ArgumentParser(
        description="Collect and analyze AI content for the knowledge base."
    )
    parser.add_argument(
        "--sources",
        type=_parse_sources,
        default=list(SUPPORTED_SOURCES),
        metavar="SOURCE[,SOURCE...]",
        help="content sources: github,rss (default: github,rss)",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=20,
        help="maximum number of collected items (default: 20)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run collection and analysis without writing files",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="enable detailed logs",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line arguments and run the pipeline."""

    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return run_pipeline(args.sources, args.limit, args.dry_run)
    except (httpx.HTTPError, OSError, ValueError, RuntimeError) as error:
        LOGGER.error("Pipeline failed: %s", error)
        if args.verbose:
            LOGGER.exception("Detailed failure")
        return 1


def _rss_feed_urls() -> tuple[str, ...]:
    configured = os.getenv("RSS_FEEDS")
    if not configured:
        return DEFAULT_RSS_FEEDS
    feeds = tuple(part.strip() for part in configured.split(",") if part.strip())
    if not feeds:
        raise ValueError("RSS_FEEDS must contain at least one URL")
    return feeds


def _distribute_limit(sources: Sequence[str], limit: int) -> dict[str, int]:
    """Distribute a total limit as evenly as possible across sources."""

    base, remainder = divmod(limit, len(sources))
    return {
        source: base + (1 if index < remainder else 0)
        for index, source in enumerate(sources)
    }


def _parse_sources(value: str) -> list[str]:
    sources = list(dict.fromkeys(part.strip().lower() for part in value.split(",")))
    if not sources or any(not source for source in sources):
        raise argparse.ArgumentTypeError("--sources must not be empty")
    unsupported = [source for source in sources if source not in SUPPORTED_SOURCES]
    if unsupported:
        allowed = ", ".join(SUPPORTED_SOURCES)
        raise argparse.ArgumentTypeError(
            f"unsupported source(s): {', '.join(unsupported)}; choose {allowed}"
        )
    return sources


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("limit must be an integer") from error
    if number <= 0:
        raise argparse.ArgumentTypeError("limit must be greater than zero")
    return number


def _parse_llm_analysis(content: str) -> dict[str, Any]:
    text = content.strip().lstrip("\ufeff")
    candidates = [text]
    fenced = re.search(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if fenced:
        candidates.append(fenced.group(1).strip())

    object_start = text.find("{")
    if object_start >= 0:
        try:
            extracted, _ = json.JSONDecoder().raw_decode(text, object_start)
        except json.JSONDecodeError:
            pass
        else:
            candidates.append(json.dumps(extracted, ensure_ascii=False))

    payload: Any = None
    parse_error: json.JSONDecodeError | None = None
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
            "LLM analysis is not valid JSON "
            f"(line {parse_error.lineno}, column {parse_error.colno}: "
            f"{parse_error.msg})"
        ) from parse_error
    if not isinstance(payload, Mapping):
        raise ValueError("LLM analysis must be a JSON object")

    summary = _clean_text(payload.get("summary"))
    score = payload.get("score")
    tags = payload.get("tags")
    if not summary:
        raise ValueError("LLM analysis has an empty summary")
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        raise ValueError("LLM analysis score must be a number")
    if not 0 <= float(score) <= 10:
        raise ValueError("LLM analysis score must be between 0 and 10")
    if not isinstance(tags, list) or not tags:
        raise ValueError("LLM analysis tags must be a non-empty list")
    clean_tags = _normalize_tags(tags)
    if not clean_tags:
        raise ValueError("LLM analysis contains no valid tags")
    return {"summary": summary, "score": round(float(score), 2), "tags": clean_tags}


def _normalize_article(item: Mapping[str, Any]) -> Article:
    source_url = _normalize_url(_clean_text(item.get("source_url")))
    source = _clean_text(item.get("source")).lower()
    stable_id = _short_hash(source_url)
    return {
        "id": stable_id,
        "title": _clean_text(item.get("title")),
        "source": source,
        "source_url": source_url,
        "published_at": _clean_text(item.get("published_at")) or None,
        "collected_at": _clean_text(item.get("collected_at")) or _utc_now(),
        "summary": _clean_text(item.get("summary")),
        "score": round(float(item.get("score", 0)), 2),
        "tags": _normalize_tags(item.get("tags", [])),
        "status": "draft",
        "analysis": {
            "provider": _clean_text(item.get("analysis_provider")),
            "model": _clean_text(item.get("analysis_model")),
        },
    }


def _validate_article(article: Mapping[str, Any]) -> None:
    for field in ("id", "title", "source", "source_url", "summary"):
        if not article.get(field):
            raise ValueError(f"article field {field!r} must not be empty")
    parsed_url = urlsplit(str(article["source_url"]))
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError(f"invalid article URL: {article['source_url']}")
    score = article.get("score")
    if not isinstance(score, (int, float)) or not 0 <= score <= 10:
        raise ValueError("article score must be between 0 and 10")
    tags = article.get("tags")
    if not isinstance(tags, list) or not tags:
        raise ValueError("article tags must be a non-empty list")


def _extract_xml_text(block: str, names: Sequence[str]) -> str:
    for name in names:
        match = re.search(
            rf"<{re.escape(name)}\b[^>]*>(.*?)</{re.escape(name)}\s*>",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            return _clean_xml_text(match.group(1))
    return ""


def _extract_link(block: str) -> str:
    match = re.search(
        r"<link\b[^>]*\bhref=[\"']([^\"']+)[\"'][^>]*/?>",
        block,
        flags=re.IGNORECASE,
    )
    if match:
        return html.unescape(match.group(1).strip())
    return _extract_xml_text(block, ("link",))


def _clean_xml_text(value: str) -> str:
    value = re.sub(r"^\s*<!\[CDATA\[(.*?)\]\]>\s*$", r"\1", value, flags=re.DOTALL)
    value = re.sub(r"<[^>]+>", " ", value)
    return _clean_text(html.unescape(value))


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _normalize_tags(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    tags: list[str] = []
    for value in values:
        tag = re.sub(r"[^a-z0-9+#.-]+", "-", _clean_text(value).lower()).strip("-")
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:5]


def _normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, "")
    )


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:60] or "article"


def _article_path(article: Mapping[str, Any]) -> Path:
    filename = f"{_slugify(str(article['title']))}-{article['id']}.json"
    return ARTICLES_DIR / filename


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    raise SystemExit(main())
