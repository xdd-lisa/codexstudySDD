from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MCP_DIR = PROJECT_ROOT / ".codex" / "mcp_servers" / "local_knowledge"
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(MCP_DIR))

import server as mcp  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "articles" / "valid_article.json"


class McpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        directory = Path(self.temporary.name)
        (directory / "article.json").write_text(
            FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
        )
        self.repository_patch = patch.object(mcp, "REPOSITORY", mcp.ArticleRepository(directory))
        self.repository_patch.start()

    def tearDown(self) -> None:
        self.repository_patch.stop()
        self.temporary.cleanup()

    def test_search_get_and_stats(self) -> None:
        self.assertEqual(mcp.search_articles("Python")[0]["id"], "0123456789abcdef")
        self.assertEqual(mcp.get_article("0123456789abcdef")["source"], "test")
        self.assertEqual(mcp.knowledge_stats()["total_articles"], 1)

    def test_tools_list(self) -> None:
        response = mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(
            [tool["name"] for tool in response["result"]["tools"]],
            ["search_articles", "get_article", "knowledge_stats"],
        )

    def test_formal_module_entrypoint(self) -> None:
        request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        process = subprocess.run(
            [sys.executable, str(MCP_DIR / "main.py")],
            input=json.dumps(request) + "\n",
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertEqual(json.loads(process.stdout)["result"]["serverInfo"]["version"], "2.0.0")


if __name__ == "__main__":
    unittest.main()
