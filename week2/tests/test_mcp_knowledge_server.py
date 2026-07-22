from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MCP_SERVER_DIR = PROJECT_ROOT / ".codex" / "mcp_servers" / "local_knowledge"
sys.path.insert(0, str(MCP_SERVER_DIR))

import main as server  # noqa: E402


class KnowledgeToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.articles_dir = Path(self.temporary_directory.name)
        articles = [
            {
                "id": "article-1",
                "title": "Python Agent Framework",
                "summary": "An AI framework for building agents.",
                "source": "github",
                "source_url": "https://example.com/1",
                "score": 9,
                "tags": ["python", "ai"],
            },
            {
                "id": "article-2",
                "title": "Model research",
                "summary": "Python techniques for model evaluation.",
                "source": "rss",
                "source_url": "https://example.com/2",
                "score": 8,
                "tags": ["python", "research"],
            },
        ]
        for index, article in enumerate(articles, start=1):
            path = self.articles_dir / f"article-{index}.json"
            path.write_text(json.dumps(article), encoding="utf-8")
        (self.articles_dir / "broken.json").write_text("{", encoding="utf-8")
        self.directory_patch = patch.object(
            server,
            "ARTICLES_DIR",
            self.articles_dir,
        )
        self.directory_patch.start()

    def tearDown(self) -> None:
        self.directory_patch.stop()
        self.temporary_directory.cleanup()

    def test_search_prioritizes_title_matches(self) -> None:
        results = server.search_articles("python")

        self.assertEqual(
            [item["id"] for item in results],
            ["article-1", "article-2"],
        )

    def test_get_article_returns_complete_record(self) -> None:
        article = server.get_article("article-2")

        self.assertEqual(
            article["summary"],
            "Python techniques for model evaluation.",
        )

    def test_get_article_reports_missing_id(self) -> None:
        with self.assertRaisesRegex(server.ToolInputError, "article not found"):
            server.get_article("missing")

    def test_stats_count_sources_and_tags(self) -> None:
        stats = server.knowledge_stats()

        self.assertEqual(stats["total_articles"], 2)
        self.assertEqual(stats["sources"], {"github": 1, "rss": 1})
        self.assertEqual(stats["popular_tags"][0], {"tag": "python", "count": 2})


class JsonRpcTests(unittest.TestCase):
    def test_tools_list_contains_three_tools(self) -> None:
        response = server.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )

        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(
            names,
            ["search_articles", "get_article", "knowledge_stats"],
        )

    def test_unknown_tool_returns_json_rpc_error(self) -> None:
        response = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "missing", "arguments": {}},
            }
        )

        self.assertEqual(response["error"]["code"], -32602)

    def test_stdio_initialize_and_stats(self) -> None:
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "knowledge_stats", "arguments": {}},
            },
        ]
        process = subprocess.run(
            [sys.executable, str(MCP_SERVER_DIR / "main.py")],
            input="".join(json.dumps(request) + "\n" for request in requests),
            text=True,
            capture_output=True,
            check=True,
        )
        responses = [json.loads(line) for line in process.stdout.splitlines()]

        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-11-25")
        stats = responses[1]["result"]["structuredContent"]
        self.assertGreater(stats["total_articles"], 0)


if __name__ == "__main__":
    unittest.main()
