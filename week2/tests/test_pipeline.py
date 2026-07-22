from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from knowledge_base import repository, schema  # noqa: E402
from pipeline import collector, pipeline, storage  # noqa: E402

SUMMARY = "这是一个长度足够的中文技术摘要，用于验证统一文章契约和流水线行为。"


class SchemaTests(unittest.TestCase):
    def test_all_production_articles_match_schema(self) -> None:
        for path in repository.ARTICLES_DIR.glob("*.json"):
            article = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(schema.validate_article(article), [], path.name)

    def test_article_filename_is_short_readable_and_stable(self) -> None:
        article = json.loads(
            (PROJECT_ROOT / "tests" / "fixtures" / "articles" / "valid_article.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            repository.article_filename(article),
            "test-python-agent-framework-01234567.json",
        )
        self.assertLessEqual(len(repository.article_filename(article)), 63)


class RssConfigurationTests(unittest.TestCase):
    def test_loads_only_enabled_yaml_sources(self) -> None:
        sources = collector.load_rss_sources()
        self.assertTrue(sources)
        self.assertTrue(all({"name", "url", "category"} == set(item) for item in sources))
        self.assertNotIn("arXiv cs.AI", {item["name"] for item in sources})


class ParseLlmAnalysisTests(unittest.TestCase):
    def test_parses_strict_json(self) -> None:
        result = pipeline._parse_llm_analysis(
            json.dumps(
                {"summary": SUMMARY, "score": 8.5, "tags": ["ai", "agents"]}, ensure_ascii=False
            )
        )
        self.assertEqual(result["score"], 8.5)

    def test_invalid_json_error_includes_location(self) -> None:
        with self.assertRaisesRegex(ValueError, r"line 1, column \d+"):
            pipeline._parse_llm_analysis('{"summary":"bad","score":8,"tags":[ai]}')


class AnalyzeItemTests(unittest.TestCase):
    def test_retries_malformed_model_output(self) -> None:
        responses = [
            SimpleNamespace(content='{"summary":"bad","score":8,"tags":[ai]}'),
            SimpleNamespace(
                content=json.dumps(
                    {"summary": SUMMARY, "score": 8, "tags": ["ai"]}, ensure_ascii=False
                ),
                provider="test",
                model="model",
            ),
        ]
        item = {
            "title": "Example",
            "source_url": "https://example.com",
            "source": "rss",
            "content": "content",
        }
        with patch.object(pipeline, "chat_with_retry", side_effect=responses) as chat:
            _, analysis = pipeline._analyze_item(object(), item)
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(analysis["tags"], ["ai"])


class StorageTests(unittest.TestCase):
    def test_raw_sequence_and_checkpoint_round_trip(self) -> None:
        now = datetime(2026, 7, 22, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(storage, "RAW_DIR", root / "raw"):
                (root / "raw").mkdir()
                (root / "raw" / "raw_20260722_001.json").write_text("[]")
                self.assertEqual(storage.next_raw_path(now).name, "raw_20260722_002.json")
            checkpoint_path = root / "checkpoint.json"
            storage.save_checkpoint(
                {"version": 1, "completed": {"x": "y"}, "failed": {}}, checkpoint_path
            )
            self.assertEqual(storage.load_checkpoint(checkpoint_path)["completed"], {"x": "y"})


class FailureIsolationTests(unittest.TestCase):
    def test_one_failed_item_does_not_block_next_item(self) -> None:
        items = [
            {
                "external_id": "one",
                "title": "Bad",
                "source": "github",
                "source_url": "https://example.com/one",
                "published_at": None,
                "collected_at": collector.utc_now(),
                "content": "x",
            },
            {
                "external_id": "two",
                "title": "Good",
                "source": "github",
                "source_url": "https://example.com/two",
                "published_at": None,
                "collected_at": collector.utc_now(),
                "content": "x",
            },
        ]
        response = SimpleNamespace(provider="test", model="model")
        repository = SimpleNamespace(load_all=lambda: [], save=lambda article: Path(article["id"]))
        client_context = MagicMock()
        client_context.__enter__.return_value = object()
        with (
            patch.object(pipeline.httpx, "Client", return_value=client_context),
            patch.object(pipeline, "collect_github", return_value=items),
            patch.object(pipeline, "save_raw"),
            patch.object(
                pipeline,
                "load_checkpoint",
                return_value={"version": 1, "completed": {}, "failed": {}},
            ),
            patch.object(pipeline, "save_checkpoint"),
            patch.object(pipeline, "record_failure") as record_failure,
            patch.object(pipeline, "create_provider", return_value=object()),
            patch.object(pipeline, "ArticleRepository", return_value=repository),
            patch.object(
                pipeline,
                "_analyze_item",
                side_effect=[
                    ValueError("broken"),
                    (response, {"summary": SUMMARY, "score": 8, "tags": ["ai"]}),
                ],
            ),
        ):
            result = pipeline.run_pipeline(["github"], 2)
        self.assertEqual(result, 0)
        record_failure.assert_called_once()
        self.assertEqual(repository.load_all(), [])


if __name__ == "__main__":
    unittest.main()
