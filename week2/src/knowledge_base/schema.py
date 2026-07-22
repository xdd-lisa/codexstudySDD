"""Canonical domain contract shared by the pipeline, hooks, and MCP server."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

ARTICLE_SCHEMA_VERSION = 1
VALID_STATUSES = {
    "draft",
    "ready",
    "published",
    "rejected",
    "failed",
}
ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9+#.-]*$")
REQUIRED_FIELDS = {
    "schema_version",
    "id",
    "title",
    "source",
    "source_url",
    "published_at",
    "collected_at",
    "summary",
    "score",
    "tags",
    "status",
    "analysis",
}


def validate_article(article: Mapping[str, Any]) -> list[str]:
    """Return every violation of the canonical article contract."""

    errors: list[str] = []
    missing = sorted(REQUIRED_FIELDS - article.keys())
    errors.extend(f"missing required field: {field}" for field in missing)

    if article.get("schema_version") != ARTICLE_SCHEMA_VERSION:
        errors.append(f"schema_version must be {ARTICLE_SCHEMA_VERSION}")

    article_id = article.get("id")
    if not isinstance(article_id, str) or not ID_PATTERN.fullmatch(article_id):
        errors.append("id must be a 16-character lowercase hexadecimal string")

    for field in ("title", "source", "summary"):
        value = article.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field} must be a non-empty string")
    summary = article.get("summary")
    if isinstance(summary, str) and len(summary.strip()) < 20:
        errors.append("summary must contain at least 20 characters")

    source_url = article.get("source_url")
    if not isinstance(source_url, str) or not _is_http_url(source_url):
        errors.append("source_url must be a valid HTTP(S) URL")

    published_at = article.get("published_at")
    if published_at is not None and not _is_timestamp(published_at):
        errors.append("published_at must be null or an ISO 8601 timestamp")
    if not _is_timestamp(article.get("collected_at")):
        errors.append("collected_at must be an ISO 8601 timestamp")

    score = article.get("score")
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not 0 <= float(score) <= 10
    ):
        errors.append("score must be a number between 0 and 10")

    tags = article.get("tags")
    if not isinstance(tags, list) or not 1 <= len(tags) <= 5:
        errors.append("tags must contain between 1 and 5 items")
    elif any(not isinstance(tag, str) or not TAG_PATTERN.fullmatch(tag) for tag in tags):
        errors.append("tags must be normalized lowercase strings")
    elif len(tags) != len(set(tags)):
        errors.append("tags must not contain duplicates")

    status = article.get("status")
    if status not in VALID_STATUSES:
        errors.append("status must be one of: " + ", ".join(sorted(VALID_STATUSES)))

    analysis = article.get("analysis")
    if not isinstance(analysis, Mapping):
        errors.append("analysis must be an object")
    else:
        for field in ("provider", "model"):
            value = analysis.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"analysis.{field} must be a non-empty string")
    return errors


def assert_valid_article(article: Mapping[str, Any]) -> None:
    """Raise ValueError when an article violates the canonical contract."""

    errors = validate_article(article)
    if errors:
        raise ValueError("invalid article: " + "; ".join(errors))


def normalize_timestamp(value: Any) -> str | None:
    """Normalize ISO 8601 or RFC 2822 source dates to ISO 8601."""

    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError) as error:
            raise ValueError(f"unsupported timestamp: {text}") from error
    return parsed.isoformat(timespec="seconds")


def _is_http_url(value: str) -> bool:
    parsed = urlsplit(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True
