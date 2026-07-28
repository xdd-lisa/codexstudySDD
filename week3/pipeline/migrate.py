"""One-time and forward-compatible migrations for stored article JSON."""

from __future__ import annotations

import json

from knowledge_base.repository import (
    ArticleRepository,
    article_filename,
    write_json_atomic,
)
from knowledge_base.schema import (
    ARTICLE_SCHEMA_VERSION,
    assert_valid_article,
    normalize_timestamp,
)


def main() -> int:
    repository = ArticleRepository()
    for path in sorted(repository.directory.glob("*.json")):
        article = json.loads(path.read_text(encoding="utf-8"))
        article["schema_version"] = ARTICLE_SCHEMA_VERSION
        article["published_at"] = normalize_timestamp(article.get("published_at"))
        article["collected_at"] = normalize_timestamp(article.get("collected_at"))
        assert_valid_article(article)
        destination = repository.directory / article_filename(article)
        if destination != path and destination.exists():
            raise ValueError(f"migration target already exists: {destination}")
        write_json_atomic(destination, article)
        if destination != path:
            path.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
