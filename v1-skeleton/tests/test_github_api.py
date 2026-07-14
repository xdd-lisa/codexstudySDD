"""Tests for the GitHub API utility."""

from __future__ import annotations

import io
import unittest
from email.message import Message
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from utils.github_api import GitHubAPIError, get_repository_info


class GetRepositoryInfoTest(unittest.TestCase):
    """Verify repository metadata fetching and failure handling."""

    @patch("utils.github_api.urlopen")
    def test_returns_repository_metadata(self, mock_urlopen) -> None:
        """Return normalized fields from a valid GitHub response."""
        mock_urlopen.return_value = io.BytesIO(
            b'{"stargazers_count": 42, "forks_count": 7, '
            b'"description": "Example repository"}'
        )

        result = get_repository_info("example", "project")

        self.assertEqual(
            result,
            {
                "stars": 42,
                "forks": 7,
                "description": "Example repository",
            },
        )
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://api.github.com/repos/example/project",
        )
        self.assertEqual(mock_urlopen.call_args.kwargs["timeout"], 10.0)

    @patch("utils.github_api.time.sleep")
    @patch("utils.github_api.urlopen")
    def test_retries_temporary_network_error(
        self,
        mock_urlopen,
        mock_sleep,
    ) -> None:
        """Retry a bounded number of times after a network failure."""
        mock_urlopen.side_effect = [
            URLError("temporary failure"),
            io.BytesIO(
                b'{"stargazers_count": 1, "forks_count": 2, '
                b'"description": null}'
            ),
        ]

        result = get_repository_info("example", "project", max_retries=1)

        self.assertEqual(
            result,
            {"stars": 1, "forks": 2, "description": None},
        )
        mock_sleep.assert_called_once_with(1)

    @patch("utils.github_api.urlopen")
    def test_does_not_retry_not_found_response(self, mock_urlopen) -> None:
        """Raise immediately when GitHub reports a missing repository."""
        mock_urlopen.side_effect = HTTPError(
            url="https://api.github.com/repos/example/missing",
            code=404,
            msg="Not Found",
            hdrs=Message(),
            fp=None,
        )

        with self.assertRaisesRegex(GitHubAPIError, "HTTP 404"):
            get_repository_info("example", "missing")

        mock_urlopen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
