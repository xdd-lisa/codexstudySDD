"""Workflow-friendly wrappers around the project's shared LLM client.

Provider configuration, retries, timeouts, and cost tracking remain in
``pipeline.model_client``.  This module only adapts that client to the simple
``(text, usage)`` and ``(JSON, usage)`` interfaces used by workflow examples.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pipeline.model_client import Usage, quick_chat

DEFAULT_SYSTEM_PROMPT = "你是一个专业的 AI 技术分析师。"


def _usage_dict(usage: Usage) -> dict[str, int | bool]:
    """Convert the shared immutable Usage value to a plain dictionary."""

    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "estimated": usage.estimated,
    }


def chat(
    prompt: str,
    system: str = DEFAULT_SYSTEM_PROMPT,
    *,
    temperature: float | None = 0.3,
    max_tokens: int | None = 2000,
) -> tuple[str, dict[str, int | bool]]:
    """Call the configured LLM and return response text plus token usage."""

    response = quick_chat(
        prompt,
        system_prompt=system,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.content, _usage_dict(response.usage)


def _strip_markdown_fence(text: str) -> str:
    """Remove one complete Markdown code fence surrounding a response."""

    cleaned = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, flags=re.DOTALL | re.I)
    return match.group(1).strip() if match else cleaned


def chat_json(
    prompt: str,
    system: str = "你是一个专业的 AI 技术分析师。只返回合法 JSON。",
    **kwargs: Any,
) -> tuple[dict[str, Any] | list[Any], dict[str, int | bool]]:
    """Call the LLM and parse a JSON object or array from its response.

    A surrounding Markdown JSON fence is tolerated. Other prose and malformed
    JSON are rejected instead of being greedily extracted, so callers never
    silently accept an unintended structure.
    """

    text, usage = chat(prompt, system=system, **kwargs)
    parsed = json.loads(_strip_markdown_fence(text))
    if not isinstance(parsed, (dict, list)):
        raise ValueError("LLM JSON response must be an object or array")
    return parsed, usage


def accumulate_usage(
    tracker: dict[str, int | float],
    new_usage: dict[str, int | bool],
) -> dict[str, int | float]:
    """Return accumulated token totals without mutating the input tracker."""

    prompt_tokens = int(tracker.get("prompt_tokens", 0)) + int(
        new_usage.get("prompt_tokens", 0)
    )
    completion_tokens = int(tracker.get("completion_tokens", 0)) + int(
        new_usage.get("completion_tokens", 0)
    )
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
