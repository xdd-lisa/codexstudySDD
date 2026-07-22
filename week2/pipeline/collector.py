"""External source collectors and RSS configuration loading."""

from __future__ import annotations

import hashlib
import html
import os
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RSS_CONFIG_PATH = PROJECT_ROOT / "pipeline" / "rss_sources.yaml"
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
GITHUB_QUERY = "topic:artificial-intelligence stars:>100"
USER_AGENT = "ai-knowledge-pipeline/1.0"
RawItem = dict[str, Any]


def load_rss_sources(path: Path = RSS_CONFIG_PATH) -> list[dict[str, str]]:
    """Load enabled RSS feeds from the project YAML configuration."""

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"could not load RSS config {path}: {error}") from error
    sources = payload.get("sources") if isinstance(payload, Mapping) else None
    if not isinstance(sources, list):
        raise ValueError("RSS config must contain a sources list")
    enabled: list[dict[str, str]] = []
    for index, source in enumerate(sources, start=1):
        if not isinstance(source, Mapping):
            raise ValueError(f"RSS source {index} must be an object")
        if not source.get("enabled", True):
            continue
        url = _clean_text(source.get("url"))
        name = _clean_text(source.get("name"))
        if not url or not name:
            raise ValueError(f"enabled RSS source {index} requires name and url")
        enabled.append(
            {
                "name": name,
                "url": url,
                "category": _clean_text(source.get("category")) or "未分类",
            }
        )
    if not enabled:
        raise ValueError("RSS config has no enabled sources")
    return enabled


def collect_github(client: httpx.Client, limit: int) -> list[RawItem]:
    """Collect repositories from GitHub Search."""

    if limit <= 0:
        return []
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token := os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    response = client.get(
        GITHUB_SEARCH_URL,
        headers=headers,
        params={"q": GITHUB_QUERY, "sort": "updated", "order": "desc", "per_page": min(limit, 100)},
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items", []) if isinstance(payload, Mapping) else []
    if not isinstance(items, list):
        raise ValueError("GitHub Search API returned an invalid items field")
    collected_at = utc_now()
    results: list[RawItem] = []
    for repository in items[:limit]:
        if not isinstance(repository, Mapping):
            continue
        title = _clean_text(repository.get("full_name"))
        url = _clean_text(repository.get("html_url"))
        if not title or not url:
            continue
        topics = repository.get("topics", [])
        description = _clean_text(repository.get("description"))
        language = _clean_text(repository.get("language"))
        content = " ".join(
            part
            for part in (
                description,
                f"Primary language: {language}." if language else "",
                f"GitHub stars: {_nonnegative_int(repository.get('stargazers_count'))}.",
            )
            if part
        )
        results.append(
            {
                "external_id": f"github:{title.lower()}",
                "title": title,
                "source": "github",
                "source_url": url,
                "published_at": _clean_text(repository.get("updated_at")),
                "collected_at": collected_at,
                "content": content,
                "source_tags": [str(topic) for topic in topics if topic]
                if isinstance(topics, list)
                else [],
            }
        )
    return results


def collect_rss(
    client: httpx.Client,
    limit: int,
    sources: Sequence[Mapping[str, str]],
) -> tuple[list[RawItem], list[dict[str, Any]]]:
    """Collect configured feeds, isolating an unavailable feed from others."""

    items: list[RawItem] = []
    failures: list[dict[str, Any]] = []
    headers = {"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml"}
    for source in sources:
        if len(items) >= limit:
            break
        try:
            response = client.get(source["url"], headers=headers)
            response.raise_for_status()
            entries = parse_rss(response.text, source["url"], limit - len(items))
        except (httpx.HTTPError, ValueError) as error:
            failures.append({"stage": "collect", "source": dict(source), "error": str(error)})
            continue
        for entry in entries:
            entry["feed_name"] = source["name"]
            entry["category"] = source["category"]
        items.extend(entries)
    return items, failures


def parse_rss(xml_text: str, feed_url: str, limit: int) -> list[RawItem]:
    """Parse the common RSS/Atom subset without a full feed dependency."""

    blocks = [
        block
        for _, block in re.findall(
            r"<(item|entry)\b[^>]*>(.*?)</\1\s*>",
            xml_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    ]
    entries: list[RawItem] = []
    for block in blocks[:limit]:
        title = _extract_xml_text(block, ("title",))
        link = _extract_link(block)
        if not title or not link:
            continue
        guid = _extract_xml_text(block, ("guid", "id"))
        entries.append(
            {
                "external_id": f"rss:{short_hash(guid or link)}",
                "title": title,
                "source": "rss",
                "source_url": link,
                "feed_url": feed_url,
                "published_at": _extract_xml_text(
                    block, ("pubDate", "published", "updated", "dc:date")
                ),
                "collected_at": utc_now(),
                "content": _extract_xml_text(
                    block, ("description", "summary", "content:encoded", "content")
                ),
                "source_tags": [],
            }
        )
    return entries


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _extract_xml_text(block: str, names: Sequence[str]) -> str:
    for name in names:
        match = re.search(
            rf"<{re.escape(name)}\b[^>]*>(.*?)</{re.escape(name)}\s*>",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            value = re.sub(r"^\s*<!\[CDATA\[(.*?)\]\]>\s*$", r"\1", match.group(1), flags=re.DOTALL)
            return _clean_text(html.unescape(re.sub(r"<[^>]+>", " ", value)))
    return ""


def _extract_link(block: str) -> str:
    match = re.search(r"<link\b[^>]*\bhref=[\"']([^\"']+)[\"'][^>]*/?>", block, flags=re.IGNORECASE)
    return html.unescape(match.group(1).strip()) if match else _extract_xml_text(block, ("link",))


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip() if value is not None else ""


def _nonnegative_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
