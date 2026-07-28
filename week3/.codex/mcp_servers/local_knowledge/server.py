"""Local-knowledge MCP protocol adapter over the shared article repository."""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Callable
from typing import Any

from knowledge_base.repository import ArticleRepository
from knowledge_base.schema import validate_article

SERVER_NAME = "local-knowledge-server"
SERVER_VERSION = "2.0.0"
PROTOCOL_VERSION = "2025-11-25"
MAX_SEARCH_LIMIT = 100
POPULAR_TAG_LIMIT = 10
JsonObject = dict[str, Any]
REPOSITORY = ArticleRepository()

TOOLS = [
    {
        "name": "search_articles",
        "description": "按关键词搜索本地知识库文章的标题和摘要。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 5},
            },
            "required": ["keyword"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_article",
        "description": "按文章 ID 获取完整文章。",
        "inputSchema": {
            "type": "object",
            "properties": {"article_id": {"type": "string", "minLength": 1}},
            "required": ["article_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "knowledge_stats",
        "description": "返回文章总数、来源分布和热门标签。",
        "inputSchema": {"type": "object", "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
]


class ToolInputError(ValueError):
    pass


class JsonRpcRequestError(ValueError):
    def __init__(self, code: int, message: str) -> None:
        self.code, self.message = code, message
        super().__init__(message)


def load_articles() -> list[JsonObject]:
    """Read valid production articles through the repository abstraction."""
    return [article for article in REPOSITORY.load_all() if not validate_article(article)]


def search_articles(keyword: str, limit: int = 5) -> list[JsonObject]:
    if not isinstance(keyword, str) or not keyword.strip():
        raise ToolInputError("keyword must be a non-empty string")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise ToolInputError("limit must be an integer between 1 and 100")
    needle = keyword.strip().casefold()
    matches = []
    for article in load_articles():
        title, summary = str(article["title"]), str(article["summary"])
        if needle in title.casefold() or needle in summary.casefold():
            matches.append(
                (
                    0 if needle in title.casefold() else 1,
                    -float(article["score"]),
                    title.casefold(),
                    _search_result(article),
                )
            )
    matches.sort(key=lambda item: item[:3])
    return [item[3] for item in matches[:limit]]


def get_article(article_id: str) -> JsonObject:
    if not isinstance(article_id, str) or not article_id.strip():
        raise ToolInputError("article_id must be a non-empty string")
    for article in load_articles():
        if article["id"] == article_id.strip():
            return article
    raise ToolInputError(f"article not found: {article_id.strip()}")


def knowledge_stats() -> JsonObject:
    articles = load_articles()
    sources = Counter(str(article["source"]) for article in articles)
    tags = Counter(tag for article in articles for tag in article["tags"])
    return {
        "total_articles": len(articles),
        "sources": dict(sorted(sources.items())),
        "popular_tags": [
            {"tag": tag, "count": count}
            for tag, count in sorted(tags.items(), key=lambda item: (-item[1], item[0]))[
                :POPULAR_TAG_LIMIT
            ]
        ],
    }


def handle_request(request: Any) -> JsonObject | None:
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid Request")
    if "id" not in request:
        return None
    request_id, method, params = request.get("id"), request.get("method"), request.get("params", {})
    if method == "initialize":
        return _success(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": "Search, inspect, and summarize the local article repository.",
            },
        )
    if method == "ping":
        return _success(request_id, {})
    if method == "tools/list":
        return _success(request_id, {"tools": TOOLS})
    if method != "tools/call" or not isinstance(params, dict):
        return _error(request_id, -32601, "Method not found")
    try:
        result = _call_tool(params)
    except JsonRpcRequestError as error:
        return _error(request_id, error.code, error.message)
    except ToolInputError as error:
        result = _tool_result({"error": str(error)}, is_error=True)
    return _success(request_id, result)


def _call_tool(params: JsonObject) -> JsonObject:
    name, arguments = params.get("name"), params.get("arguments", {})
    handlers: dict[str, Callable[..., Any]] = {
        "search_articles": search_articles,
        "get_article": get_article,
        "knowledge_stats": knowledge_stats,
    }
    if name not in handlers or not isinstance(arguments, dict):
        raise JsonRpcRequestError(-32602, "invalid tool call")
    expected = {
        "search_articles": {"keyword", "limit"},
        "get_article": {"article_id"},
        "knowledge_stats": set(),
    }[name]
    if unexpected := set(arguments) - expected:
        raise JsonRpcRequestError(-32602, "unexpected arguments: " + ", ".join(sorted(unexpected)))
    try:
        return _tool_result(handlers[name](**arguments))
    except TypeError as error:
        raise ToolInputError(str(error)) from error


def _search_result(article: JsonObject) -> JsonObject:
    return {
        key: article[key]
        for key in (
            "id",
            "title",
            "summary",
            "source",
            "source_url",
            "published_at",
            "score",
            "tags",
        )
    }


def _tool_result(payload: Any, *, is_error: bool = False) -> JsonObject:
    result: JsonObject = {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "structuredContent": payload if isinstance(payload, dict) else {"result": payload},
    }
    if is_error:
        result["isError"] = True
    return result


def _success(request_id: Any, result: Any) -> JsonObject:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> JsonObject:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
        except json.JSONDecodeError:
            response = _error(None, -32700, "Parse error")
        if response is not None:
            print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)


def main() -> int:
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
