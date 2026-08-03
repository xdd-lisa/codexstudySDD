from __future__ import annotations

import unittest
from unittest.mock import patch

from pipeline.model_client import LLMResponse, Usage
from workflows import model_client


class WorkflowModelClientTests(unittest.TestCase):
    def test_chat_adapts_shared_response(self) -> None:
        response = LLMResponse(
            content="回答",
            usage=Usage(10, 5, 15),
            provider="test",
            model="test-model",
        )
        with patch.object(model_client, "quick_chat", return_value=response) as mocked:
            text, usage = model_client.chat("问题", system="系统", max_tokens=50)

        self.assertEqual(text, "回答")
        self.assertEqual(usage["total_tokens"], 15)
        self.assertEqual(mocked.call_args.kwargs["system_prompt"], "系统")
        self.assertEqual(mocked.call_args.kwargs["max_tokens"], 50)

    def test_chat_json_accepts_complete_markdown_fence(self) -> None:
        with patch.object(
            model_client,
            "chat",
            return_value=("```json\n{\"intent\": \"general_chat\"}\n```", {}),
        ):
            parsed, usage = model_client.chat_json("分类")

        self.assertEqual(parsed, {"intent": "general_chat"})
        self.assertEqual(usage, {})

    def test_chat_json_rejects_surrounding_prose(self) -> None:
        with patch.object(
            model_client,
            "chat",
            return_value=("结果如下：{\"intent\": \"general_chat\"}", {}),
        ):
            with self.assertRaises(ValueError):
                model_client.chat_json("分类")


if __name__ == "__main__":
    unittest.main()
