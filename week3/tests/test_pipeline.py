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


class GithubTrendingCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.markup = (
            PROJECT_ROOT / "tests" / "fixtures" / "github_trending_weekly.html"
        ).read_text(encoding="utf-8")

    def test_parses_weekly_evidence_and_deduplicates_identity(self) -> None:
        candidates = collector.parse_github_trending(self.markup)
        self.assertEqual(
            [candidate["repository"] for candidate in candidates],
            ["example/alpha-ai", "example/beta-agent", "example/awesome-ai"],
        )
        self.assertEqual(candidates[0]["period_stars"], 1000)
        self.assertEqual(candidates[0]["rank"], 1)
        self.assertEqual(candidates[0]["url"], "https://github.com/example/alpha-ai")

    def test_rejects_missing_or_malformed_trending_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "no repository entries"):
            collector.parse_github_trending("<html></html>")
        with self.assertRaisesRegex(ValueError, "lacks repository identity or weekly stars"):
            collector.parse_github_trending(
                '<article><a href="/example/ai">example/ai</a></article>'
            )

    def test_filters_enriches_normalizes_and_bounds_requests(self) -> None:
        metadata = [
            _github_metadata(
                "example/alpha-ai",
                "Large language model inference runtime",
                stars=5000,
            ),
            _github_metadata(
                "example/beta-agent",
                "AI agent orchestration framework",
                stars=2500,
            ),
            _github_metadata(
                "example/awesome-ai",
                "Awesome curated list of AI links",
                stars=9000,
            ),
        ]
        responses = [
            SimpleNamespace(text=self.markup, raise_for_status=lambda: None),
            *[
                SimpleNamespace(
                    json=lambda payload=payload: payload,
                    raise_for_status=lambda: None,
                )
                for payload in metadata
            ],
        ]
        client = MagicMock()
        client.get.side_effect = responses
        with patch.dict("os.environ", {"GITHUB_TOKEN": "secret-token"}):
            items = collector.collect_github_trending(client, 2)
        self.assertEqual(
            [item["external_id"] for item in items],
            ["github:example/alpha-ai", "github:example/beta-agent"],
        )
        self.assertEqual([item["popularity"] for item in items], [100, 50])
        self.assertEqual(items[0]["source"], "github_trending")
        self.assertEqual(
            items[0]["source_metrics"]["period_stars"],
            items[0]["popularity_raw"],
        )
        self.assertNotIn("secret-token", json.dumps(items))
        self.assertEqual(client.get.call_count, 4)
        self.assertEqual(
            client.get.call_args_list[1].kwargs["headers"]["Authorization"],
            "Bearer secret-token",
        )

    def test_relevance_missing_metadata_and_stable_tie_break(self) -> None:
        self.assertTrue(
            collector.is_ai_repository(
                {"full_name": "example/runtime", "topics": ["llm-inference"]}
            )
        )
        self.assertFalse(
            collector.is_ai_repository(
                {
                    "full_name": "example/awesome-ai",
                    "description": "Awesome AI resource list",
                    "topics": ["llm"],
                }
            )
        )
        self.assertFalse(collector.is_ai_repository({"full_name": "example/tool"}))
        tied = [
            {
                "source_url": "https://github.com/z/repo",
                "popularity_raw": 5,
            },
            {
                "source_url": "https://github.com/a/repo",
                "popularity_raw": 5,
            },
        ]
        ranked = collector.rank_github_trending(tied, 5)
        self.assertEqual(
            [item["source_url"] for item in ranked],
            ["https://github.com/a/repo", "https://github.com/z/repo"],
        )
        self.assertEqual(len(ranked), 2)


class ContainerAiCollectorTests(unittest.TestCase):
    def test_collects_two_windows_filters_deduplicates_and_ranks(self) -> None:
        now = datetime(2026, 7, 29, 12, tzinfo=UTC)
        alpha = _container_metadata(
            "example/alpha",
            "Kubernetes inference serving for LLM models",
            stars=100,
            created_at="2026-07-28T00:00:00Z",
            pushed_at="2026-07-28T00:00:00Z",
        )
        beta = _container_metadata(
            "example/beta",
            "GPU model training in OCI containers",
            stars=200,
            created_at="2025-01-01T00:00:00Z",
            pushed_at="2026-07-29T00:00:00Z",
        )
        created_items = [
            alpha,
            {
                **_container_metadata(
                    "example/container-only",
                    "Kubernetes container runtime",
                    stars=900,
                ),
                "topics": ["kubernetes"],
            },
            _container_metadata(
                "example/archived-ai",
                "Docker LLM inference",
                stars=800,
                archived=True,
            ),
            _container_metadata(
                "example/tutorial",
                "Kubernetes AI tutorial",
                stars=700,
            ),
        ]
        pushed_items = [
            {**alpha, "full_name": "EXAMPLE/ALPHA"},
            beta,
            {
                **_container_metadata(
                    "example/ai-only",
                    "LLM model inference",
                    stars=600,
                ),
                "topics": ["llm"],
            },
            _container_metadata(
                "example/old",
                "Kubernetes AI model serving",
                stars=500,
                created_at="2025-01-01T00:00:00Z",
                pushed_at="2026-07-01T00:00:00Z",
            ),
        ]
        client = MagicMock()
        client.get.side_effect = [
            _json_response({"items": created_items}),
            _json_response({"items": pushed_items}),
        ]
        items = collector.collect_container_ai(client, 20, now=now)
        self.assertEqual(
            [item["external_id"] for item in items],
            ["github:example/beta", "github:example/alpha"],
        )
        self.assertEqual(client.get.call_count, 2)
        self.assertIn("created:>=2026-07-22T12:00:00Z", client.get.call_args_list[0].kwargs["params"]["q"])
        self.assertIn("pushed:>=2026-07-22T12:00:00Z", client.get.call_args_list[1].kwargs["params"]["q"])
        self.assertEqual(
            items[0]["source_metrics"]["metric"],
            "current_total_stars",
        )
        self.assertEqual(items[0]["source_metrics"]["stars_total"], 200)
        self.assertNotIn("period_stars", items[0]["source_metrics"])
        self.assertEqual(
            items[1]["source_metrics"]["matched_windows"],
            ["created", "pushed"],
        )

    def test_relevance_window_and_malformed_search(self) -> None:
        cutoff = datetime(2026, 7, 22, tzinfo=UTC)
        self.assertTrue(
            collector.is_container_ai_repository(
                {
                    "full_name": "example/runtime",
                    "description": "K8s GPU inference serving",
                }
            )
        )
        self.assertFalse(
            collector.is_container_ai_repository(
                {"full_name": "example/kubernetes", "description": "OCI runtime"}
            )
        )
        self.assertFalse(
            collector.is_container_ai_repository(
                {"full_name": "example/model", "description": "LLM inference"}
            )
        )
        self.assertFalse(
            collector.is_container_ai_repository(
                {
                    "full_name": "example/awesome",
                    "description": "Awesome Kubernetes AI resource list",
                }
            )
        )
        self.assertTrue(
            collector.repository_in_container_ai_window(
                {"created_at": "2026-07-22T00:00:00Z"}, cutoff
            )
        )
        self.assertFalse(
            collector.repository_in_container_ai_window(
                {
                    "created_at": "invalid",
                    "pushed_at": "2026-07-21T23:59:59Z",
                },
                cutoff,
            )
        )
        client = MagicMock()
        client.get.side_effect = [
            _json_response({"items": []}),
            _json_response({"items": "invalid"}),
        ]
        with self.assertRaisesRegex(ValueError, "pushed search"):
            collector.collect_container_ai(
                client,
                15,
                now=datetime(2026, 7, 29, tzinfo=UTC),
            )

    def test_top_fifteen_ties_and_undersized_results(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=UTC)
        repositories = [
            _container_metadata(
                f"example/repo-{index:02d}",
                "Docker container for AI model inference",
                stars=100 if index < 2 else 100 - index,
            )
            for index in range(20)
        ]
        client = MagicMock()
        client.get.side_effect = [
            _json_response({"items": list(reversed(repositories))}),
            _json_response({"items": []}),
        ]
        items = collector.collect_container_ai(client, 20, now=now)
        self.assertEqual(len(items), 15)
        self.assertEqual(
            [item["source_url"] for item in items[:2]],
            [
                "https://github.com/example/repo-00",
                "https://github.com/example/repo-01",
            ],
        )

        client.get.side_effect = [
            _json_response({"items": repositories[:1]}),
            _json_response({"items": []}),
        ]
        self.assertEqual(len(collector.collect_container_ai(client, 15, now=now)), 1)


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

    def test_weekly_container_ai_path_and_atomic_no_overwrite(self) -> None:
        now = datetime(2026, 7, 29, 11, 30, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            weekly_dir = Path(temporary) / "weekly"
            with patch.object(storage, "WEEKLY_DIR", weekly_dir):
                path = storage.weekly_container_ai_path(now)
                self.assertEqual(path.name, "260729-1930-cai.json")
                self.assertEqual(len(path.stem), 15)
                saved = storage.save_weekly_container_ai([{"id": "one"}], now=now)
                self.assertEqual(saved, path)
                self.assertEqual(
                    json.loads(path.read_text(encoding="utf-8")),
                    [{"id": "one"}],
                )
                with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
                    storage.save_weekly_container_ai([{"id": "two"}], now=now)
                self.assertEqual(
                    json.loads(path.read_text(encoding="utf-8")),
                    [{"id": "one"}],
                )
                self.assertEqual(list(weekly_dir.glob(f".{path.name}.*.tmp")), [])

    def test_weekly_dry_run_and_atomic_overwrite_mode(self) -> None:
        now = datetime(2026, 7, 29, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(storage, "WEEKLY_DIR", root / "weekly"):
                self.assertIsNone(
                    storage.save_weekly_container_ai([], now=now, dry_run=True)
                )
                self.assertFalse((root / "weekly").exists())
            replaceable = root / "replaceable.json"
            storage.write_json_atomic(replaceable, {"version": 1})
            storage.write_json_atomic(replaceable, {"version": 2})
            self.assertEqual(
                json.loads(replaceable.read_text(encoding="utf-8")),
                {"version": 2},
            )

    def test_weekly_path_rejects_naive_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone"):
            storage.weekly_container_ai_path(datetime(2026, 7, 29))


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


class GithubTrendingPipelineTests(unittest.TestCase):
    def test_cli_source_is_opt_in_and_preserves_defaults(self) -> None:
        parser = pipeline.build_parser()
        self.assertEqual(parser.parse_args([]).sources, ["github", "rss"])
        self.assertEqual(
            parser.parse_args(["--sources", "github-trending"]).sources,
            ["github-trending"],
        )

    def test_selected_source_reaches_processing_and_checkpoint(self) -> None:
        item = {
            "external_id": "github:example/alpha-ai",
            "title": "example/alpha-ai",
            "source": "github_trending",
            "source_url": "https://github.com/example/alpha-ai",
            "published_at": "2026-07-28T00:00:00Z",
            "collected_at": collector.utc_now(),
            "content": "Large language model inference runtime",
            "source_tags": ["llm"],
        }
        response = SimpleNamespace(provider="test", model="model")
        repository = SimpleNamespace(load_all=lambda: [], save=MagicMock())
        client_context = MagicMock()
        client_context.__enter__.return_value = object()
        with (
            patch.object(pipeline.httpx, "Client", return_value=client_context),
            patch.object(
                pipeline, "collect_github_trending", return_value=[item]
            ) as collect_trending,
            patch.object(pipeline, "collect_github") as collect_github,
            patch.object(pipeline, "save_raw") as save_raw,
            patch.object(
                pipeline,
                "load_checkpoint",
                return_value={"version": 1, "completed": {}, "failed": {}},
            ),
            patch.object(pipeline, "save_checkpoint") as save_checkpoint,
            patch.object(pipeline, "create_provider", return_value=object()),
            patch.object(pipeline, "ArticleRepository", return_value=repository),
            patch.object(
                pipeline,
                "_analyze_item",
                return_value=(
                    response,
                    {"summary": SUMMARY, "score": 8, "tags": ["ai"]},
                ),
            ),
        ):
            result = pipeline.run_pipeline(["github-trending"], 1)
        self.assertEqual(result, 0)
        collect_trending.assert_called_once()
        collect_github.assert_not_called()
        save_raw.assert_called_once_with([item], dry_run=False)
        save_checkpoint.assert_called_once()
        repository.save.assert_called_once()

    def test_trending_failure_does_not_block_rss(self) -> None:
        client_context = MagicMock()
        client_context.__enter__.return_value = object()
        with (
            patch.object(pipeline.httpx, "Client", return_value=client_context),
            patch.object(
                pipeline,
                "collect_github_trending",
                side_effect=ValueError("missing weekly evidence"),
            ),
            patch.object(pipeline, "load_rss_sources", return_value=[]),
            patch.object(pipeline, "collect_rss", return_value=([], [])) as collect_rss,
            patch.object(pipeline, "save_raw"),
            patch.object(pipeline, "record_failure") as record_failure,
            patch.object(
                pipeline,
                "load_checkpoint",
                return_value={"version": 1, "completed": {}, "failed": {}},
            ),
        ):
            result = pipeline.run_pipeline(["github-trending", "rss"], 2)
        self.assertEqual(result, 0)
        collect_rss.assert_called_once()
        record_failure.assert_called_once()
        self.assertEqual(
            record_failure.call_args.args[0]["source"], "github-trending"
        )


class ContainerAiPipelineTests(unittest.TestCase):
    def test_cli_source_is_opt_in_and_defaults_are_unchanged(self) -> None:
        parser = pipeline.build_parser()
        self.assertEqual(parser.parse_args([]).sources, ["github", "rss"])
        self.assertEqual(
            parser.parse_args(["--sources", "container-ai"]).sources,
            ["container-ai"],
        )

    def test_weekly_snapshot_precedes_common_raw_and_checkpoint(self) -> None:
        item = _container_raw_item()
        response = SimpleNamespace(provider="test", model="model")
        article_repository = SimpleNamespace(load_all=lambda: [], save=MagicMock())
        client_context = MagicMock()
        client_context.__enter__.return_value = object()
        events: list[str] = []
        with (
            patch.object(pipeline.httpx, "Client", return_value=client_context),
            patch.object(pipeline, "collect_container_ai", return_value=[item]),
            patch.object(
                pipeline,
                "save_weekly_container_ai",
                side_effect=lambda *args, **kwargs: events.append("weekly"),
            ) as save_weekly,
            patch.object(
                pipeline,
                "save_raw",
                side_effect=lambda *args, **kwargs: events.append("raw"),
            ) as save_raw,
            patch.object(
                pipeline,
                "load_checkpoint",
                return_value={"version": 1, "completed": {}, "failed": {}},
            ),
            patch.object(pipeline, "save_checkpoint") as save_checkpoint,
            patch.object(pipeline, "create_provider", return_value=object()),
            patch.object(
                pipeline, "ArticleRepository", return_value=article_repository
            ),
            patch.object(
                pipeline,
                "_analyze_item",
                return_value=(
                    response,
                    {"summary": SUMMARY, "score": 8, "tags": ["ai"]},
                ),
            ),
        ):
            result = pipeline.run_pipeline(["container-ai"], 20)
        self.assertEqual(result, 0)
        self.assertEqual(events, ["weekly", "raw"])
        self.assertEqual(save_weekly.call_args.args[0], [item])
        self.assertFalse(save_weekly.call_args.kwargs["dry_run"])
        save_raw.assert_called_once_with([item], dry_run=False)
        save_checkpoint.assert_called_once()
        article_repository.save.assert_called_once()

    def test_dry_run_passes_through_without_weekly_write(self) -> None:
        item = _container_raw_item()
        response = SimpleNamespace(provider="test", model="model")
        article_repository = SimpleNamespace(load_all=lambda: [], save=MagicMock())
        client_context = MagicMock()
        client_context.__enter__.return_value = object()
        with (
            patch.object(pipeline.httpx, "Client", return_value=client_context),
            patch.object(pipeline, "collect_container_ai", return_value=[item]),
            patch.object(pipeline, "save_weekly_container_ai") as save_weekly,
            patch.object(pipeline, "save_raw"),
            patch.object(pipeline, "create_provider", return_value=object()),
            patch.object(
                pipeline, "ArticleRepository", return_value=article_repository
            ),
            patch.object(
                pipeline,
                "_analyze_item",
                return_value=(
                    response,
                    {"summary": SUMMARY, "score": 8, "tags": ["ai"]},
                ),
            ),
        ):
            result = pipeline.run_pipeline(["container-ai"], 15, dry_run=True)
        self.assertEqual(result, 0)
        self.assertTrue(save_weekly.call_args.kwargs["dry_run"])
        article_repository.save.assert_not_called()

    def test_search_failure_does_not_block_rss_or_leak_token(self) -> None:
        client_context = MagicMock()
        client_context.__enter__.return_value = object()
        with (
            patch.dict("os.environ", {"GITHUB_TOKEN": "secret-token"}),
            patch.object(pipeline.httpx, "Client", return_value=client_context),
            patch.object(
                pipeline,
                "collect_container_ai",
                side_effect=ValueError("created search failed"),
            ),
            patch.object(pipeline, "load_rss_sources", return_value=[]),
            patch.object(pipeline, "collect_rss", return_value=([], [])) as collect_rss,
            patch.object(pipeline, "save_raw"),
            patch.object(pipeline, "record_failure") as record_failure,
            patch.object(
                pipeline,
                "load_checkpoint",
                return_value={"version": 1, "completed": {}, "failed": {}},
            ),
        ):
            result = pipeline.run_pipeline(["container-ai", "rss"], 2)
        self.assertEqual(result, 0)
        collect_rss.assert_called_once()
        failure = record_failure.call_args.args[0]
        self.assertEqual(failure["source"], "container-ai")
        self.assertNotIn("secret-token", json.dumps(failure))

    def test_weekly_storage_failure_discards_container_items_and_continues_rss(
        self,
    ) -> None:
        item = _container_raw_item()
        client_context = MagicMock()
        client_context.__enter__.return_value = object()
        with (
            patch.object(pipeline.httpx, "Client", return_value=client_context),
            patch.object(pipeline, "collect_container_ai", return_value=[item]),
            patch.object(
                pipeline,
                "save_weekly_container_ai",
                side_effect=FileExistsError("collision"),
            ),
            patch.object(pipeline, "load_rss_sources", return_value=[]),
            patch.object(pipeline, "collect_rss", return_value=([], [])) as collect_rss,
            patch.object(pipeline, "save_raw") as save_raw,
            patch.object(pipeline, "record_failure") as record_failure,
            patch.object(
                pipeline,
                "load_checkpoint",
                return_value={"version": 1, "completed": {}, "failed": {}},
            ),
        ):
            result = pipeline.run_pipeline(["container-ai", "rss"], 2)
        self.assertEqual(result, 0)
        collect_rss.assert_called_once()
        save_raw.assert_called_once_with([], dry_run=False)
        record_failure.assert_called_once()


def _github_metadata(
    full_name: str,
    description: str,
    *,
    stars: int,
) -> dict[str, object]:
    return {
        "full_name": full_name,
        "name": full_name.split("/", 1)[1],
        "description": description,
        "topics": ["llm"],
        "language": "Python",
        "license": {"spdx_id": "MIT"},
        "stargazers_count": stars,
        "forks_count": 100,
        "updated_at": "2026-07-28T00:00:00Z",
        "pushed_at": "2026-07-27T23:00:00Z",
    }


def _container_metadata(
    full_name: str,
    description: str,
    *,
    stars: int,
    created_at: str = "2026-07-28T00:00:00Z",
    pushed_at: str = "2026-07-28T00:00:00Z",
    archived: bool = False,
) -> dict[str, object]:
    return {
        "full_name": full_name,
        "name": full_name.split("/", 1)[1],
        "html_url": f"https://github.com/{full_name}",
        "description": description,
        "topics": ["kubernetes", "inference"],
        "language": "Python",
        "stargazers_count": stars,
        "forks_count": 10,
        "archived": archived,
        "created_at": created_at,
        "pushed_at": pushed_at,
        "updated_at": pushed_at,
    }


def _json_response(payload: object) -> SimpleNamespace:
    return SimpleNamespace(
        json=lambda: payload,
        raise_for_status=lambda: None,
    )


def _container_raw_item() -> dict[str, object]:
    return {
        "external_id": "github:example/container-ai",
        "title": "example/container-ai",
        "source": "container_ai",
        "source_url": "https://github.com/example/container-ai",
        "published_at": "2026-07-28T00:00:00+00:00",
        "collected_at": "2026-07-29T12:00:00+00:00",
        "content": "Kubernetes LLM inference serving",
        "source_tags": ["kubernetes", "llm"],
    }


if __name__ == "__main__":
    unittest.main()
