"""Shared knowledge-base domain model and repository."""

from .repository import ArticleRepository
from .schema import ARTICLE_SCHEMA_VERSION, assert_valid_article, validate_article

__all__ = [
    "ARTICLE_SCHEMA_VERSION",
    "ArticleRepository",
    "assert_valid_article",
    "validate_article",
]
