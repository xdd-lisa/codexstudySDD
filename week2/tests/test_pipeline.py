from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "pipeline"
sys.path.insert(0, str(PIPELINE_DIR))

import pipeline  # noqa: E402


class ParseLlmAnalysisTests(unittest.TestCase):
    def test_parses_strict_json(self) -> None:
        result = pipeline._parse_llm_analysis(
            '{"summary":"简要总结","score":8.5,"tags":["ai","agents"]}'
        )

        self.assertEqual(
            result,
            {"summary": "简要总结", "score": 8.5, "tags": ["ai", "agents"]},
        )

    def test_extracts_json_from_fence_and_surrounding_text(self) -> None:
        result = pipeline._parse_llm_analysis(
            "Result follows:\n```json\n"
            '{"summary":"简要总结","score":8,"tags":["ai"]}'
            "\n```\nDone."
        )

        self.assertEqual(result["score"], 8.0)

    def test_invalid_json_error_includes_location(self) -> None:
        with self.assertRaisesRegex(ValueError, r"line 1, column \d+"):
            pipeline._parse_llm_analysis(
                '{"summary":"简要总结","score":8,"tags":[ai]}'
            )


class AnalyzeItemTests(unittest.TestCase):
    def test_retries_malformed_model_output(self) -> None:
        responses = [
            SimpleNamespace(content='{"summary":"总结","score":8,"tags":[ai]}'),
            SimpleNamespace(
                content='{"summary":"总结","score":8,"tags":["ai"]}',
                provider="test",
                model="test-model",
            ),
        ]
        item = {
            "title": "Example",
            "source_url": "https://example.com",
            "source": "rss",
            "content": "Example content",
        }

        with patch.object(pipeline, "chat_with_retry", side_effect=responses) as chat:
            response, analysis = pipeline._analyze_item(object(), item)

        self.assertEqual(chat.call_count, 2)
        self.assertEqual(response.provider, "test")
        self.assertEqual(analysis["tags"], ["ai"])
        retry_messages = chat.call_args.args[1]
        self.assertEqual(retry_messages[-2]["role"], "assistant")
        self.assertEqual(retry_messages[-1]["role"], "user")


class SaveRawTests(unittest.TestCase):
    def test_uses_daily_incrementing_file_names(self) -> None:
        now = datetime(2026, 7, 22, 1, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw_dir = Path(temporary_directory)
            (raw_dir / "raw_20260722_001.json").write_text("[]")
            (raw_dir / "raw_20260722_notes.json").write_text("[]")

            with patch.object(pipeline, "RAW_DIR", raw_dir):
                destination = pipeline._next_raw_path(now)

        self.assertEqual(destination.name, "raw_20260722_002.json")

    def test_save_raw_starts_sequence_at_one(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            raw_dir = Path(temporary_directory) / "raw"
            with (
                patch.object(pipeline, "RAW_DIR", raw_dir),
                patch.object(
                    pipeline,
                    "_next_raw_path",
                    return_value=raw_dir / "raw_20260722_001.json",
                ),
            ):
                destination = pipeline.save_raw([{"title": "Example"}])

            self.assertEqual(destination, raw_dir / "raw_20260722_001.json")
            self.assertTrue(destination.is_file())


if __name__ == "__main__":
    unittest.main()
