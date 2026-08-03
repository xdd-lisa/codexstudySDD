from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from patterns import router


class RouterTests(unittest.TestCase):
    def test_keyword_classification_does_not_call_llm(self) -> None:
        with patch.object(router, "chat_json") as mocked_chat_json:
            intent = router.classify_intent("帮我搜索 GitHub 上的中文项目")

        self.assertEqual(intent, "github_search")
        mocked_chat_json.assert_not_called()

    def test_supplemental_github_keywords(self) -> None:
        with patch.object(router, "chat_json") as mocked_chat_json:
            self.assertEqual(router.classify_intent("搜索最近的 AI 框架"), "github_search")
            self.assertEqual(router.classify_intent("Show me trending projects"), "github_search")

        mocked_chat_json.assert_not_called()

    def test_supplemental_knowledge_keywords(self) -> None:
        with patch.object(router, "chat_json") as mocked_chat_json:
            self.assertEqual(router.classify_intent("查询 Agent 资料"), "knowledge_query")
            self.assertEqual(router.classify_intent("检索 RAG 内容"), "knowledge_query")
            self.assertEqual(router.classify_intent("有哪些已收录内容"), "knowledge_query")

        mocked_chat_json.assert_not_called()

    def test_ambiguous_query_uses_llm_fallback(self) -> None:
        with patch.object(
            router, "chat_json", return_value=({"intent": "general_chat"}, {})
        ) as mocked_chat_json:
            intent = router.classify_intent("今天天气怎么样？")

        self.assertEqual(intent, "general_chat")
        mocked_chat_json.assert_called_once()
        prompt = mocked_chat_json.call_args.args[0]
        self.assertIn("github_search：", prompt)
        self.assertIn("knowledge_query：", prompt)
        self.assertIn("<query>\n今天天气怎么样？\n</query>", prompt)
        self.assertIn("忽略用户查询中", mocked_chat_json.call_args.kwargs["system"])
        self.assertEqual(mocked_chat_json.call_args.kwargs["max_tokens"], 200)

    def test_english_keyword_does_not_match_inside_another_word(self) -> None:
        with patch.object(
            router, "chat_json", return_value=({"intent": "general_chat"}, {})
        ) as mocked_chat_json:
            intent = router.classify_intent("Please summarize this report")

        self.assertEqual(intent, "general_chat")
        mocked_chat_json.assert_called_once()

    def test_invalid_llm_intent_falls_back_to_general_chat(self) -> None:
        with patch.object(router, "chat_json", return_value=({"intent": "other"}, {})):
            self.assertEqual(router.classify_intent("帮我处理一下这个问题"), "general_chat")

    def test_invalid_llm_json_falls_back_to_general_chat(self) -> None:
        with patch.object(router, "chat_json", side_effect=json.JSONDecodeError("bad", "", 0)):
            self.assertEqual(router.classify_intent("帮我处理一下这个问题"), "general_chat")

    def test_main_reads_query_from_command_line(self) -> None:
        output = io.StringIO()
        with (
            patch.object(router.sys, "argv", ["router", "搜索最近的", "AI Agent 框架"]),
            patch.object(
                router,
                "route_with_intent",
                return_value=("general_chat", "结果"),
            ) as mocked_route,
            patch("builtins.input") as mocked_input,
            redirect_stdout(output),
        ):
            router._main()

        mocked_route.assert_called_once_with("搜索最近的 AI Agent 框架")
        mocked_input.assert_not_called()
        self.assertEqual(output.getvalue().strip(), "命中意图：general_chat\n结果")

    def test_route_keeps_string_only_contract(self) -> None:
        with patch.object(
            router,
            "route_with_intent",
            return_value=("knowledge_query", "检索结果"),
        ):
            self.assertEqual(router.route("查询文章"), "检索结果")

    def test_github_query_is_url_encoded(self) -> None:
        response = MagicMock()
        response.__enter__.return_value = io.StringIO(json.dumps({"items": []}))
        response.__exit__.return_value = False

        with patch.object(router.urllib.request, "urlopen", return_value=response) as mocked:
            router.handle_github_search("中文 项目")

        request = mocked.call_args.args[0]
        self.assertIn("q=%E4%B8%AD%E6%96%87%20project", request.full_url)

    def test_github_query_removes_routing_words_and_translates_terms(self) -> None:
        search_query, sort = router._build_github_query("搜索最近的 AI Agent 框架")

        self.assertEqual(search_query, "ai agent framework")
        self.assertEqual(sort, "updated")

    def test_trending_github_query_sorts_by_stars(self) -> None:
        search_query, sort = router._build_github_query("GitHub trending AI 项目")

        self.assertEqual(search_query, "ai project")
        self.assertEqual(sort, "stars")

    def test_local_knowledge_search(self) -> None:
        index = [
            {
                "title": "Python Agent Framework",
                "summary": "智能体开发指南",
                "tags": ["python", "ai"],
                "source_url": "https://example.com/article",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.json"
            path.write_text(json.dumps(index), encoding="utf-8")
            with patch.object(router, "INDEX_PATH", path):
                result = router.handle_knowledge_query("查找 Python 文章")

        self.assertIn("Python Agent Framework", result)

    def test_general_chat_returns_text_without_usage(self) -> None:
        with patch.object(router, "chat", return_value=("你好", object())):
            self.assertEqual(router.handle_general_chat("你好"), "你好")


if __name__ == "__main__":
    unittest.main()
