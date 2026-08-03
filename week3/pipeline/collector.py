"""External source collectors and RSS configuration loading."""

from __future__ import annotations

import hashlib
import html
import os
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import quote

import httpx
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RSS_CONFIG_PATH = PROJECT_ROOT / "pipeline" / "rss_sources.yaml"
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
GITHUB_TRENDING_URL = "https://github.com/trending"
GITHUB_REPOSITORY_URL = "https://api.github.com/repos"
GITHUB_QUERY = "topic:artificial-intelligence stars:>100"
USER_AGENT = "ai-knowledge-pipeline/1.0"
GITHUB_TRENDING_PERIOD = "weekly"
GITHUB_TRENDING_MAX_CANDIDATES = 100
GITHUB_TRENDING_CANDIDATE_MULTIPLIER = 3
CONTAINER_AI_DEFAULT_LIMIT = 15
CONTAINER_AI_SEARCH_MULTIPLIER = 4
CONTAINER_AI_SEARCH_MINIMUM = 30
CONTAINER_AI_QUERY = '"container ai" OR "kubernetes ai" OR "docker ai" OR "k8s inference"'
CONTAINER_TERMS = (
    "container",
    "containers",
    "kubernetes",
    "k8s",
    "docker",
    "oci",
    "containerd",
    "cri",
)
AI_LIFECYCLE_TERMS = (
    "ai",
    "ml",
    "llm",
    "machine learning",
    "model training",
    "inference",
    "serving",
    "model deployment",
    "gpu",
    "accelerator",
)
CONTAINER_AI_EXCLUSION_TERMS = (
    "awesome",
    "curated list",
    "resource list",
    "link collection",
    "tutorial",
    "course",
    "workshop",
)
AI_RELEVANCE_TERMS = (
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "generative ai",
    "large language model",
    "language model",
    "llm",
    "agentic",
    "ai agent",
    "multi-agent",
    "transformer",
    "diffusion model",
    "model training",
    "model inference",
    "model evaluation",
    "rag",
    "retrieval augmented",
    "vector database",
    "embedding",
    "pytorch",
    "tensorflow",
)
RESOURCE_LIST_TERMS = (
    "awesome",
    "curated list",
    "resource list",
    "resources list",
    "link collection",
    "link index",
    "navigation directory",
)
RawItem = dict[str, Any]


class TrendingCandidate(TypedDict):
    """Required evidence parsed from one GitHub Trending entry."""

    repository: str
    url: str
    rank: int
    period_stars: int


def github_headers() -> dict[str, str]:
    """Build safe public GitHub headers with optional environment authorization."""

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token := os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def canonical_github_repository(value: str) -> tuple[str, str]:
    """Return lowercase owner/repository identity and its canonical public URL."""

    repository = _clean_text(value).strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError(f"invalid GitHub repository identity: {value!r}")
    identity = repository.lower()
    return identity, f"https://github.com/{identity}"


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
    response = client.get(
        GITHUB_SEARCH_URL,
        headers=github_headers(),
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


def collect_container_ai(
    client: httpx.Client,
    limit: int,
    *,
    now: datetime | None = None,
) -> list[RawItem]:
    """Collect recently created or pushed Container AI repositories."""

    result_limit = min(limit, CONTAINER_AI_DEFAULT_LIMIT)
    if result_limit <= 0:
        return []
    collected_time = now or datetime.now(UTC)
    if collected_time.tzinfo is None:
        raise ValueError("Container AI collection time must include timezone information")
    collected_utc = collected_time.astimezone(UTC)
    cutoff = collected_utc - timedelta(days=7)
    cutoff_query = cutoff.isoformat(timespec="seconds").replace("+00:00", "Z")
    request_limit = min(
        max(result_limit * CONTAINER_AI_SEARCH_MULTIPLIER, CONTAINER_AI_SEARCH_MINIMUM),
        100,
    )
    merged: dict[str, dict[str, Any]] = {}
    for window in ("created", "pushed"):
        response = client.get(
            GITHUB_SEARCH_URL,
            headers=github_headers(),
            params={
                "q": f"{CONTAINER_AI_QUERY} {window}:>={cutoff_query}",
                "sort": "stars",
                "order": "desc",
                "per_page": request_limit,
            },
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") if isinstance(payload, Mapping) else None
        if not isinstance(items, list):
            raise ValueError(f"GitHub {window} search returned an invalid items field")
        for repository in items:
            if not isinstance(repository, Mapping):
                continue
            try:
                identity, url = canonical_github_repository(
                    _clean_text(repository.get("full_name"))
                )
            except ValueError:
                continue
            existing = merged.get(identity)
            windows = (
                set(existing["matched_windows"])
                if existing is not None
                else set()
            )
            windows.add(window)
            candidate = dict(repository)
            candidate["canonical_url"] = url
            candidate["matched_windows"] = sorted(windows)
            if existing is None or _nonnegative_int(
                candidate.get("stargazers_count")
            ) >= _nonnegative_int(existing.get("stargazers_count")):
                merged[identity] = candidate
            else:
                existing["matched_windows"] = sorted(windows)

    selected: list[tuple[str, Mapping[str, Any]]] = []
    for identity, repository in merged.items():
        if not repository_in_container_ai_window(repository, cutoff):
            continue
        if not is_container_ai_repository(repository):
            continue
        selected.append((identity, repository))
    selected.sort(
        key=lambda pair: (
            -_nonnegative_int(pair[1].get("stargazers_count")),
            _clean_text(pair[1].get("canonical_url")),
        )
    )
    collected_at = collected_utc.isoformat(timespec="seconds")
    return [
        container_ai_raw_item(identity, repository, collected_at, cutoff_query)
        for identity, repository in selected[:result_limit]
    ]


def repository_in_container_ai_window(
    repository: Mapping[str, Any],
    cutoff: datetime,
) -> bool:
    """Return whether repository creation or push time is inside the window."""

    return any(
        timestamp is not None and timestamp >= cutoff
        for timestamp in (
            _parse_github_timestamp(repository.get("created_at")),
            _parse_github_timestamp(repository.get("pushed_at")),
        )
    )


def is_container_ai_repository(metadata: Mapping[str, Any]) -> bool:
    """Apply deterministic dual-domain relevance and exclusion rules."""

    if metadata.get("archived") is True:
        return False
    topics = metadata.get("topics")
    topic_text = (
        " ".join(_clean_text(topic) for topic in topics)
        if isinstance(topics, list)
        else ""
    )
    evidence = " ".join(
        (
            _clean_text(metadata.get("full_name")),
            _clean_text(metadata.get("name")),
            _clean_text(metadata.get("description")),
            topic_text,
        )
    ).lower()
    if _contains_term(evidence, CONTAINER_AI_EXCLUSION_TERMS):
        return False
    return _contains_term(evidence, CONTAINER_TERMS) and _contains_term(
        evidence, AI_LIFECYCLE_TERMS
    )


def container_ai_raw_item(
    identity: str,
    metadata: Mapping[str, Any],
    collected_at: str,
    cutoff: str,
) -> RawItem:
    """Convert one selected Container AI repository to the shared raw contract."""

    _, canonical_url = canonical_github_repository(identity)
    topics = metadata.get("topics")
    source_tags = (
        [_clean_text(topic).lower() for topic in topics if _clean_text(topic)]
        if isinstance(topics, list)
        else []
    )
    description = _clean_text(metadata.get("description"))
    language = _clean_text(metadata.get("language"))
    stars = _nonnegative_int(metadata.get("stargazers_count"))
    content = " ".join(
        part
        for part in (
            description,
            f"Primary language: {language}." if language else "",
            f"GitHub current total stars: {stars}.",
        )
        if part
    )
    return {
        "external_id": f"github:{identity}",
        "title": identity,
        "source": "container_ai",
        "source_url": canonical_url,
        "published_at": _clean_text(metadata.get("created_at")) or None,
        "collected_at": collected_at,
        "content": content,
        "source_tags": source_tags,
        "source_metrics": {
            "metric": "current_total_stars",
            "stars_total": stars,
            "forks_total": _nonnegative_int(metadata.get("forks_count")),
            "candidate_window": "created_or_pushed_last_7_days",
            "window_cutoff": cutoff,
            "matched_windows": list(metadata.get("matched_windows", [])),
            "created_at": _clean_text(metadata.get("created_at")) or None,
            "pushed_at": _clean_text(metadata.get("pushed_at")) or None,
            "updated_at": _clean_text(metadata.get("updated_at")) or None,
            "query_methods": ["github_search_created", "github_search_pushed"],
        },
    }


def collect_github_trending(client: httpx.Client, limit: int) -> list[RawItem]:
    """Collect weekly GitHub Trending repositories with traceable ranking evidence."""

    if limit <= 0:
        return []
    response = client.get(
        GITHUB_TRENDING_URL,
        headers={"Accept": "text/html", "User-Agent": USER_AGENT},
        params={"since": GITHUB_TRENDING_PERIOD},
    )
    response.raise_for_status()
    candidates = parse_github_trending(response.text)
    request_limit = min(
        len(candidates),
        max(limit, limit * GITHUB_TRENDING_CANDIDATE_MULTIPLIER),
        GITHUB_TRENDING_MAX_CANDIDATES,
    )
    collected_at = utc_now()
    eligible: list[RawItem] = []
    seen_repositories: set[str] = set()
    seen_urls: set[str] = set()
    enrichment_successes = 0
    enrichment_failures = 0
    for candidate in candidates[:request_limit]:
        repository = candidate["repository"]
        url = candidate["url"]
        if repository in seen_repositories or url in seen_urls:
            continue
        seen_repositories.add(repository)
        seen_urls.add(url)
        try:
            metadata = fetch_github_repository(client, repository)
        except (httpx.HTTPError, ValueError):
            enrichment_failures += 1
            continue
        enrichment_successes += 1
        if not is_ai_repository(metadata):
            continue
        eligible.append(
            github_trending_raw_item(candidate, metadata, collected_at)
        )
    if enrichment_failures and not enrichment_successes:
        raise ValueError("GitHub repository metadata enrichment failed for all candidates")
    return rank_github_trending(eligible, limit)


def parse_github_trending(html_text: str) -> list[TrendingCandidate]:
    """Parse bounded weekly rank evidence from GitHub Trending markup."""

    if not isinstance(html_text, str) or not html_text.strip():
        raise ValueError("GitHub Trending returned empty markup")
    articles = re.findall(
        r"<article\b[^>]*>(.*?)</article\s*>",
        html_text[:2_000_000],
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not articles:
        raise ValueError("GitHub Trending markup contains no repository entries")
    candidates: list[TrendingCandidate] = []
    seen: set[str] = set()
    for rank, article in enumerate(articles[:GITHUB_TRENDING_MAX_CANDIDATES], start=1):
        repository_match = re.search(
            r"href=[\"']/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:[/?#\"'])",
            article,
            flags=re.IGNORECASE,
        )
        stars_match = re.search(
            r"([\d,]+)\s+stars?\s+this\s+week",
            _clean_text(html.unescape(re.sub(r"<[^>]+>", " ", article))),
            flags=re.IGNORECASE,
        )
        if repository_match is None or stars_match is None:
            raise ValueError(
                f"GitHub Trending entry {rank} lacks repository identity or weekly stars"
            )
        repository, url = canonical_github_repository(repository_match.group(1))
        if repository in seen:
            continue
        seen.add(repository)
        candidates.append(
            {
                "repository": repository,
                "url": url,
                "rank": rank,
                "period_stars": int(stars_match.group(1).replace(",", "")),
            }
        )
    if not candidates:
        raise ValueError("GitHub Trending contains no valid weekly repository evidence")
    return candidates


def fetch_github_repository(client: httpx.Client, repository: str) -> Mapping[str, Any]:
    """Fetch and validate public metadata for one canonical GitHub repository."""

    identity, _ = canonical_github_repository(repository)
    response = client.get(
        f"{GITHUB_REPOSITORY_URL}/{quote(identity, safe='/')}",
        headers=github_headers(),
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise ValueError(f"GitHub repository metadata for {identity} must be an object")
    full_name, _ = canonical_github_repository(_clean_text(payload.get("full_name")))
    if full_name != identity:
        raise ValueError(f"GitHub repository metadata identity mismatch for {identity}")
    return payload


def is_ai_repository(metadata: Mapping[str, Any]) -> bool:
    """Return whether public repository metadata directly evidences AI relevance."""

    topics = metadata.get("topics")
    normalized_topics = (
        [_clean_text(topic).lower() for topic in topics]
        if isinstance(topics, list)
        else []
    )
    evidence = " ".join(
        (
            _clean_text(metadata.get("full_name")),
            _clean_text(metadata.get("name")),
            _clean_text(metadata.get("description")),
            " ".join(normalized_topics),
        )
    ).lower()
    if any(term in evidence for term in RESOURCE_LIST_TERMS):
        return False
    return any(term in evidence for term in AI_RELEVANCE_TERMS)


def github_trending_raw_item(
    candidate: TrendingCandidate,
    metadata: Mapping[str, Any],
    collected_at: str,
) -> RawItem:
    """Convert Trending evidence and repository metadata to the pipeline raw contract."""

    topics = metadata.get("topics")
    source_tags = (
        [_clean_text(topic).lower() for topic in topics if _clean_text(topic)]
        if isinstance(topics, list)
        else []
    )
    description = _clean_text(metadata.get("description"))
    language = _clean_text(metadata.get("language"))
    license_payload = metadata.get("license")
    license_name = (
        _clean_text(license_payload.get("spdx_id"))
        if isinstance(license_payload, Mapping)
        else ""
    )
    period_stars = candidate["period_stars"]
    content = " ".join(
        part
        for part in (
            description,
            f"Primary language: {language}." if language else "",
            f"GitHub stars this week: {period_stars}.",
            f"GitHub total stars: {_nonnegative_int(metadata.get('stargazers_count'))}.",
        )
        if part
    )
    return {
        "external_id": f"github:{candidate['repository']}",
        "title": candidate["repository"],
        "source": "github_trending",
        "source_url": candidate["url"],
        "published_at": _clean_text(metadata.get("updated_at")) or None,
        "collected_at": collected_at,
        "content": content,
        "source_tags": source_tags,
        "popularity": 0,
        "popularity_raw": period_stars,
        "popularity_unit": "stars_this_week",
        "popularity_method": "linear_relative_to_batch_max",
        "source_metrics": {
            "rank": candidate["rank"],
            "period": GITHUB_TRENDING_PERIOD,
            "period_days": 7,
            "period_stars": period_stars,
            "stars_total": _nonnegative_int(metadata.get("stargazers_count")),
            "forks_total": _nonnegative_int(metadata.get("forks_count")),
            "description": description or None,
            "primary_language": language or None,
            "topics": source_tags,
            "license": license_name or None,
            "updated_at": _clean_text(metadata.get("updated_at")) or None,
            "recent_activity": {
                "pushed_at": _clean_text(metadata.get("pushed_at")) or None,
                "method": "repository_pushed_at",
            },
            "method": "github_trending_weekly_page",
        },
    }


def rank_github_trending(items: Sequence[RawItem], limit: int) -> list[RawItem]:
    """Normalize same-window period stars and return stable, bounded results."""

    if limit <= 0 or not items:
        return []
    max_stars = max(_nonnegative_int(item.get("popularity_raw")) for item in items)
    if max_stars <= 0:
        raise ValueError("GitHub Trending period stars must include a positive value")
    ranked: list[RawItem] = []
    for item in items:
        normalized = dict(item)
        period_stars = _nonnegative_int(normalized.get("popularity_raw"))
        normalized["popularity"] = round(period_stars / max_stars * 100)
        ranked.append(normalized)
    ranked.sort(
        key=lambda item: (
            -_nonnegative_int(item.get("popularity")),
            _clean_text(item.get("source_url")),
        )
    )
    return ranked[:limit]


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


def _parse_github_timestamp(value: Any) -> datetime | None:
    text = _clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _contains_term(text: str, terms: Sequence[str]) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    return any(
        re.search(rf"(?:^|\s){re.escape(term)}(?:$|\s)", normalized)
        for term in terms
    )
