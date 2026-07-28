"""Filesystem repository for canonical production articles."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .schema import assert_valid_article

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTICLES_DIR = PROJECT_ROOT / "knowledge" / "articles"


class ArticleRepository:
    """Filesystem repository; its interface can later hide an index/cache."""

    def __init__(self, directory: Path = ARTICLES_DIR) -> None:
        self.directory = directory

    def load_all(self) -> list[dict[str, Any]]:
        articles: list[dict[str, Any]] = []
        if not self.directory.is_dir():
            return articles
        for path in sorted(self.directory.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                articles.append(value)
        return articles

    def save(self, article: Mapping[str, Any]) -> Path:
        assert_valid_article(article)
        self.directory.mkdir(parents=True, exist_ok=True)
        filename = article_filename(article)
        path = self.directory / filename
        write_json_atomic(path, dict(article))
        return path


def write_json_atomic(path: Path, payload: Any) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def article_filename(article: Mapping[str, Any]) -> str:
    """Return ``source-short-title-id8.json`` for a canonical article."""

    source = slugify(str(article.get("source", "")), max_length=12, fallback="source")
    title = slugify(str(article.get("title", "")), max_length=32, fallback="article")
    article_id = str(article.get("id", ""))[:8]
    return f"{source}-{title}-{article_id}.json"


def slugify(value: str, *, max_length: int, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized[:max_length].rstrip("-") or fallback
