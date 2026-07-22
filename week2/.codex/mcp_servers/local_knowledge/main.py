#!/usr/bin/env python3
"""Expose the local knowledge base as a dependency-free MCP stdio server."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable


SERVER_NAME = "local-knowledge-server"
SERVER_VERSION = "1.0.0"
LATEST_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = {
    LATEST_PROTOCOL_VERSION,
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
}
PROJECT_ROOT = Path(__file__).resolve().parents[3]
ARTICLES_DIR = PROJECT_ROOT / "knowledge" / "articles"
MAX_SEARCH_LIMIT = 100
POPULAR_TAG_LIMIT = 10

JsonObject = dict[str, Any]


TOOLS: list[JsonObject] = [
    {
        "name": "search_articles",
        "title": "Search knowledge articles",
        "description": "按关键词搜索本地知识库文章的标题和摘要。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "minLength": 1,
                    "description": "要搜索的关键词，不区分大小写。",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_SEARCH_LIMIT,
                    "default": 5,
                    "description": "最多返回的文章数量。",
                },
            },
            "required": ["keyword"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_article",
        "title": "Get a knowledge article",
        "description": "按文章 ID 获取本地知识库中的完整文章数据。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "article_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "文章 JSON 中的 id 字段。",
                }
            },
            "required": ["article_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "knowledge_stats",
        "title": "Get knowledge base statistics",
        "description": "返回文章总数、来源分布和前 10 个热门标签。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
]


class ToolInputError(ValueError):
    """An error that should be returned as an MCP tool execution error."""


class JsonRpcRequestError(ValueError):
    """A malformed MCP request that should use a JSON-RPC error response."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def load_articles(directory: Path | None = None) -> list[JsonObject]:
    """Load every valid JSON object from the articles directory."""

    directory = directory or ARTICLES_DIR
    articles: list[JsonObject] = []
    if not directory.is_dir():
        print(f"Article directory does not exist: {directory}", file=sys.stderr)
        return articles

    for path in sorted(directory.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as article_file:
                article = json.load(article_file)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            print(f"Skipping unreadable article {path}: {error}", file=sys.stderr)
            continue
        if not isinstance(article, dict):
            print(f"Skipping non-object article {path}", file=sys.stderr)
            continue
        articles.append(article)
    return articles


def search_articles(keyword: str, limit: int = 5) -> list[JsonObject]:
    """Search article titles and summaries using a case-insensitive keyword."""

    if not isinstance(keyword, str) or not keyword.strip():
        raise ToolInputError("keyword must be a non-empty string")
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ToolInputError("limit must be an integer")
    if not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise ToolInputError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")

    needle = keyword.strip().casefold()
    matches: list[tuple[int, float, str, JsonObject]] = []
    for article in load_articles():
        title = _string_value(article.get("title"))
        summary = _string_value(article.get("summary"))
        title_match = needle in title.casefold()
        summary_match = needle in summary.casefold()
        if not title_match and not summary_match:
            continue
        score = _numeric_score(article.get("score"))
        matches.append(
            (
                0 if title_match else 1,
                -score,
                title.casefold(),
                _search_result(article),
            )
        )

    matches.sort(key=lambda item: item[:3])
    return [item[3] for item in matches[:limit]]


def get_article(article_id: str) -> JsonObject:
    """Return the complete article whose ID matches ``article_id``."""

    if not isinstance(article_id, str) or not article_id.strip():
        raise ToolInputError("article_id must be a non-empty string")
    requested_id = article_id.strip()
    for article in load_articles():
        if str(article.get("id", "")) == requested_id:
            return article
    raise ToolInputError(f"article not found: {requested_id}")


def knowledge_stats() -> JsonObject:
    """Return article count, source distribution, and popular tags."""

    articles = load_articles()
    sources: Counter[str] = Counter()
    tags: Counter[str] = Counter()
    for article in articles:
        source = _string_value(article.get("source")).strip() or "unknown"
        sources[source] += 1
        article_tags = article.get("tags", [])
        if isinstance(article_tags, list):
            tags.update(
                normalized
                for tag in article_tags
                if (normalized := _string_value(tag).strip())
            )

    return {
        "total_articles": len(articles),
        "sources": dict(sorted(sources.items())),
        "popular_tags": [
            {"tag": tag, "count": count}
            for tag, count in sorted(
                tags.items(),
                key=lambda item: (-item[1], item[0].casefold()),
            )[:POPULAR_TAG_LIMIT]
        ],
    }


def handle_request(request: Any) -> JsonObject | None:
    """Handle one decoded JSON-RPC message."""

    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return _error_response(None, -32600, "Invalid Request")

    request_id = request.get("id")
    is_notification = "id" not in request
    method = request.get("method")
    if not isinstance(method, str):
        return None if is_notification else _error_response(
            request_id, -32600, "Invalid Request"
        )

    if is_notification:
        return None

    params = request.get("params", {})
    if not isinstance(params, dict):
        return _error_response(request_id, -32602, "Invalid params")

    if method == "initialize":
        return _success_response(request_id, _initialize_result(params))
    if method == "ping":
        return _success_response(request_id, {})
    if method == "tools/list":
        return _success_response(request_id, {"tools": TOOLS})
    if method == "tools/call":
        try:
            result = _call_tool(params)
        except JsonRpcRequestError as error:
            return _error_response(request_id, error.code, error.message)
        except ToolInputError as error:
            result = _tool_result({"error": str(error)}, is_error=True)
        except Exception as error:  # Keep protocol output valid on local I/O errors.
            print(f"Tool execution failed: {error}", file=sys.stderr)
            result = _tool_result(
                {"error": "internal tool execution error"},
                is_error=True,
            )
        return _success_response(request_id, result)

    return _error_response(request_id, -32601, "Method not found")


def serve() -> None:
    """Read newline-delimited JSON-RPC messages from stdin until EOF."""

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as error:
            response = _error_response(
                None,
                -32700,
                "Parse error",
                {"line": error.lineno, "column": error.colno},
            )
        else:
            response = handle_request(request)
        if response is not None:
            print(
                json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                flush=True,
            )


def _initialize_result(params: JsonObject) -> JsonObject:
    requested_version = params.get("protocolVersion")
    protocol_version = (
        requested_version
        if requested_version in SUPPORTED_PROTOCOL_VERSIONS
        else LATEST_PROTOCOL_VERSION
    )
    return {
        "protocolVersion": protocol_version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {
            "name": SERVER_NAME,
            "title": "Local Knowledge Server",
            "version": SERVER_VERSION,
            "description": "Search and inspect the local JSON knowledge base.",
        },
        "instructions": (
            "Use search_articles to discover article IDs, get_article for a "
            "complete record, and knowledge_stats for collection statistics."
        ),
    }


def _call_tool(params: JsonObject) -> JsonObject:
    name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(name, str) or not name:
        raise JsonRpcRequestError(-32602, "tool name must be a non-empty string")
    if not isinstance(arguments, dict):
        raise JsonRpcRequestError(-32602, "tool arguments must be an object")

    handlers: dict[str, Callable[..., Any]] = {
        "search_articles": search_articles,
        "get_article": get_article,
        "knowledge_stats": knowledge_stats,
    }
    handler = handlers.get(name)
    if handler is None:
        raise JsonRpcRequestError(-32602, f"unknown tool: {name}")

    expected_arguments = {
        "search_articles": {"keyword", "limit"},
        "get_article": {"article_id"},
        "knowledge_stats": set(),
    }[name]
    unexpected = sorted(set(arguments) - expected_arguments)
    if unexpected:
        raise JsonRpcRequestError(
            -32602,
            f"unexpected argument(s): {', '.join(unexpected)}",
        )

    try:
        payload = handler(**arguments)
    except TypeError as error:
        raise ToolInputError(f"invalid arguments for {name}: {error}") from error
    return _tool_result(payload)


def _tool_result(payload: Any, *, is_error: bool = False) -> JsonObject:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    structured_content = (
        payload if isinstance(payload, dict) else {"result": payload}
    )
    result: JsonObject = {
        "content": [{"type": "text", "text": text}],
        "structuredContent": structured_content,
    }
    if is_error:
        result["isError"] = True
    return result


def _search_result(article: JsonObject) -> JsonObject:
    fields = (
        "id",
        "title",
        "summary",
        "source",
        "source_url",
        "published_at",
        "score",
        "tags",
    )
    return {field: article.get(field) for field in fields if field in article}


def _string_value(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _numeric_score(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _success_response(request_id: Any, result: Any) -> JsonObject:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(
    request_id: Any,
    code: int,
    message: str,
    data: Any | None = None,
) -> JsonObject:
    error: JsonObject = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


if __name__ == "__main__":
    serve()
