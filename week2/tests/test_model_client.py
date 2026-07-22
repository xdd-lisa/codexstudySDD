from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import httpx

from pipeline import model_client


class CostTrackerTests(unittest.TestCase):
    def test_accumulates_usage_and_estimates_cost(self) -> None:
        tracker = model_client.CostTracker()

        tracker.record(model_client.Usage(1_000_000, 500_000, 1_500_000), "deepseek")
        tracker.record(model_client.Usage(500_000, 500_000, 1_000_000), "deepseek")

        self.assertEqual(tracker.estimated_cost("deepseek"), 3.5)

    def test_report_prints_usage_and_cost(self) -> None:
        tracker = model_client.CostTracker()
        tracker.record(model_client.Usage(1_000, 200, 1_200), "qwen")

        output = io.StringIO()
        with redirect_stdout(output):
            tracker.report("qwen")

        report = output.getvalue()
        self.assertIn("Calls: 1", report)
        self.assertIn("Input tokens: 1000", report)
        self.assertIn("Estimated cost: ¥0.006400", report)

    def test_chat_records_only_successful_response(self) -> None:
        def handle_request(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "model": "deepseek-test",
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                },
                request=request,
            )

        provider = model_client.OpenAICompatibleProvider(
            provider_name="deepseek",
            api_key="test-key",
            base_url="https://example.com",
            model="deepseek-test",
            transport=httpx.MockTransport(handle_request),
        )
        isolated_tracker = model_client.CostTracker()

        with patch.object(model_client, "tracker", isolated_tracker):
            provider.chat([{"role": "user", "content": "hello"}])

        self.assertEqual(isolated_tracker.estimated_cost("deepseek"), 0.00002)


if __name__ == "__main__":
    unittest.main()
