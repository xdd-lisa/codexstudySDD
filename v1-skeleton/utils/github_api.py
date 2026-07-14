"""Helpers for reading repository metadata from the GitHub REST API."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RETRIES = 2
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class RepositoryInfo(TypedDict):
    """Basic metadata returned for a GitHub repository."""

    stars: int
    forks: int
    description: str | None


class GitHubAPIError(RuntimeError):
    """Raised when repository metadata cannot be fetched or validated."""


class _RetryableGitHubAPIError(GitHubAPIError):
    """An internal error that may succeed when retried."""


def get_repository_info(
    owner: str,
    repository: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> RepositoryInfo:
    """Fetch basic information for a repository from the GitHub API.

    If the ``GITHUB_TOKEN`` environment variable is set, its value is used for
    authentication. Authentication is optional for public repositories, but it
    provides a higher API rate limit.

    Args:
        owner: GitHub account or organization that owns the repository.
        repository: Repository name without the owner prefix.
        timeout: Timeout in seconds for each HTTP request.
        max_retries: Maximum retries after the initial request for temporary
            network failures, rate limiting, server errors, or invalid payloads.

    Returns:
        A dictionary containing ``stars``, ``forks``, and ``description``.

    Raises:
        ValueError: If an argument is empty or outside its allowed range.
        GitHubAPIError: If GitHub rejects the request, all attempts fail, or the
            response does not contain valid repository metadata.
    """
    normalized_owner = owner.strip()
    normalized_repository = repository.strip()
    if not normalized_owner:
        raise ValueError("owner must not be empty")
    if not normalized_repository:
        raise ValueError("repository must not be empty")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if max_retries < 0:
        raise ValueError("max_retries must not be negative")

    url = (
        f"{GITHUB_API_URL}/repos/{quote(normalized_owner, safe='')}"
        f"/{quote(normalized_repository, safe='')}"
    )
    request = Request(url, headers=_build_headers(), method="GET")

    for attempt in range(max_retries + 1):
        try:
            return _fetch_repository_info(request, timeout)
        except _RetryableGitHubAPIError as error:
            if attempt == max_retries:
                raise GitHubAPIError(
                    f"GitHub API request failed after {attempt + 1} attempts"
                ) from error

            delay = 2**attempt
            LOGGER.warning(
                "Temporary GitHub API failure; retrying in %s second(s) "
                "(attempt %s/%s)",
                delay,
                attempt + 1,
                max_retries + 1,
            )
            time.sleep(delay)

    raise GitHubAPIError("GitHub API request failed unexpectedly")


def _build_headers() -> dict[str, str]:
    """Build GitHub API headers without exposing credentials to callers."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-knowledge-base-assistant",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_repository_info(
    request: Request,
    timeout: float,
) -> RepositoryInfo:
    """Execute one GitHub API request and validate its response."""
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as error:
        if error.code in RETRYABLE_STATUS_CODES or _is_rate_limited(error):
            raise _RetryableGitHubAPIError(
                f"GitHub API temporarily returned HTTP {error.code}"
            ) from error
        raise GitHubAPIError(
            f"GitHub API returned HTTP {error.code} for the repository"
        ) from error
    except URLError as error:
        raise _RetryableGitHubAPIError(
            "A network error occurred while calling the GitHub API"
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
        raise _RetryableGitHubAPIError(
            "GitHub API returned an invalid JSON response"
        ) from error

    try:
        stars = payload["stargazers_count"]
        forks = payload["forks_count"]
        description = payload["description"]
    except (KeyError, TypeError) as error:
        raise _RetryableGitHubAPIError(
            "GitHub API response is missing repository metadata"
        ) from error

    if (
        not isinstance(stars, int)
        or isinstance(stars, bool)
        or stars < 0
        or not isinstance(forks, int)
        or isinstance(forks, bool)
        or forks < 0
        or description is not None
        and not isinstance(description, str)
    ):
        raise _RetryableGitHubAPIError(
            "GitHub API returned repository metadata with invalid types"
        )

    return {"stars": stars, "forks": forks, "description": description}


def _is_rate_limited(error: HTTPError) -> bool:
    """Return whether an HTTP 403 response represents API rate limiting."""
    return (
        error.code == 403
        and error.headers.get("X-RateLimit-Remaining") == "0"
    )
