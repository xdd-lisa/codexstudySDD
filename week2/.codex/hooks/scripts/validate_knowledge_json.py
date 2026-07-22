#!/usr/bin/env python3
"""Run the week2 JSON validator for knowledge article tool targets."""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


VALIDATION_TIMEOUT_SECONDS = 10
MAX_AGENT_FIX_ATTEMPTS = 3
DIRECT_PATH_KEYS = {"file", "file_path", "filePath", "path", "filename"}
WRITE_TOOL_NAMES = {
    "apply_patch",
    "edit",
    "edit_file",
    "write",
    "write_file",
}
PATCH_PATH_PATTERN = re.compile(
    r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$",
    re.MULTILINE,
)
COMMAND_PATH_PATTERN = re.compile(
    r"(?:[^\s\"'`;|&<>]+/)*knowledge/articles/"
    r"[^\s\"'`;|&<>/]+\.json"
)


def read_event() -> dict[str, Any] | None:
    """Read a Codex hook event from standard input."""
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return None
    return event if isinstance(event, dict) else None


def collect_direct_paths(value: Any) -> list[str]:
    """Collect path-like values from common tool argument fields."""
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in DIRECT_PATH_KEYS and isinstance(item, str):
                paths.append(item)
            elif isinstance(item, (dict, list)):
                paths.extend(collect_direct_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.extend(collect_direct_paths(item))
    return paths


def collect_command_paths(tool_input: Any) -> list[str]:
    """Extract article paths from apply_patch text or shell commands."""
    if not isinstance(tool_input, dict):
        return []

    paths: list[str] = []
    for key in ("command", "patch", "patchText", "patch_text"):
        value = tool_input.get(key)
        if not isinstance(value, str):
            continue
        paths.extend(PATCH_PATH_PATTERN.findall(value))
        if ".codex/hooks/scripts/validate_json.py" not in value:
            paths.extend(COMMAND_PATH_PATTERN.findall(value))
    return paths


def is_article_json(path: Path) -> bool:
    """Return whether path directly matches knowledge/articles/*.json."""
    parts = path.parts
    for index in range(len(parts) - 2):
        if parts[index : index + 2] == ("knowledge", "articles"):
            return index + 3 == len(parts) and path.suffix.lower() == ".json"
    return False


def resolve_paths(candidates: list[str], cwd: Path) -> list[Path]:
    """Resolve candidates and expand globs into unique existing files."""
    resolved: list[Path] = []
    seen: set[Path] = set()

    for candidate_text in candidates:
        cleaned = candidate_text.strip().strip("\"'")
        candidate = Path(cleaned)
        if not candidate.is_absolute():
            candidate = cwd / candidate

        matches = [Path(item) for item in glob.glob(str(candidate))]
        if not matches and candidate.exists():
            matches = [candidate]

        for match in matches:
            absolute = match.resolve()
            if absolute.is_file() and is_article_json(absolute):
                if absolute not in seen:
                    resolved.append(absolute)
                    seen.add(absolute)

    return resolved


def find_validator(target: Path, cwd: Path) -> Path | None:
    """Find the project JSON validator from the target or session directory."""
    search_roots = [target.parent, *target.parents, cwd, *cwd.parents]
    seen: set[Path] = set()
    for root in search_roots:
        if root in seen:
            continue
        seen.add(root)
        validator = (
            root / ".codex" / "hooks" / "scripts" / "validate_json.py"
        )
        if validator.is_file():
            return validator
    return None


def validate_file(path: Path, cwd: Path) -> str | None:
    """Validate one file and return feedback when validation fails."""
    validator = find_validator(path, cwd)
    if validator is None:
        return f"{path}: .codex/hooks/scripts/validate_json.py not found"

    try:
        result = subprocess.run(
            ["python3", str(validator), str(path)],
            cwd=str(validator.parent.parent),
            capture_output=True,
            check=False,
            text=True,
            timeout=VALIDATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return (
            f"{path}: validation timed out after "
            f"{VALIDATION_TIMEOUT_SECONDS} seconds"
        )
    except OSError as error:
        return f"{path}: could not start validator: {error}"

    if result.returncode == 0:
        return None

    output = "\n".join(
        part.strip() for part in (result.stderr, result.stdout) if part.strip()
    )
    return output or f"{path}: validator exited with {result.returncode}"


def apply_safe_fixes(path: Path) -> tuple[list[str], str | None]:
    """Apply deterministic normalizations without inventing semantic data."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return [], None
    if not isinstance(data, dict):
        return [], None

    fixes: list[str] = []
    entry_id = data.get("id")
    if isinstance(entry_id, str):
        normalized_id = entry_id.strip()
        if normalized_id.isdecimal() and int(normalized_id) > 0:
            data["id"] = int(normalized_id)
            fixes.append("converted id to a positive integer")

    for field_name in ("title", "summary", "source_url"):
        value = data.get(field_name)
        if isinstance(value, str) and value != value.strip():
            data[field_name] = value.strip()
            fixes.append(f"trimmed {field_name}")

    for field_name in ("status", "audience"):
        value = data.get(field_name)
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if value != normalized:
                data[field_name] = normalized
                fixes.append(f"normalized {field_name}")

    tags = data.get("tags")
    if isinstance(tags, list):
        normalized_tags: list[str] = []
        seen_tags: set[str] = set()
        for tag in tags:
            if not isinstance(tag, str):
                continue
            normalized_tag = tag.strip().casefold()
            if normalized_tag and normalized_tag not in seen_tags:
                normalized_tags.append(normalized_tag)
                seen_tags.add(normalized_tag)
        if normalized_tags and normalized_tags != tags:
            data["tags"] = normalized_tags
            fixes.append("normalized and deduplicated tags")

    score = data.get("score")
    if isinstance(score, str):
        try:
            normalized_score = float(score.strip())
        except ValueError:
            normalized_score = -1.0
        if 0 <= normalized_score <= 1:
            data["score"] = normalized_score
            fixes.append("converted score to a number")

    collected_at = data.get("collected_at")
    if "timestamp" not in data and isinstance(collected_at, str):
        if "T" in collected_at and (
            collected_at.endswith("Z")
            or re.search(r"[+-]\d{2}:\d{2}$", collected_at)
        ):
            data["timestamp"] = collected_at
            fixes.append("copied collected_at to timestamp")

    if not fixes:
        return [], None

    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(path)
    except OSError as error:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        return [], f"{path}: automatic fix could not be saved: {error}"
    return fixes, None


def attempt_state_path(path: Path) -> Path:
    """Return a temporary state path for one article file."""
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
    return (
        Path(tempfile.gettempdir())
        / "codex-knowledge-json-validator"
        / f"{digest}.json"
    )


def record_failed_write(path: Path, turn_id: str) -> int:
    """Record and return consecutive failed writes within one Codex turn."""
    state_path = attempt_state_path(path)
    attempts = 0
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(state, dict) and state.get("turn_id") == turn_id:
            attempts = int(state.get("attempts", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass

    attempts += 1
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({"turn_id": turn_id, "attempts": attempts}),
            encoding="utf-8",
        )
    except OSError:
        return attempts
    return attempts


def clear_failed_writes(path: Path) -> None:
    """Clear retry state after an article validates successfully."""
    try:
        attempt_state_path(path).unlink(missing_ok=True)
    except OSError:
        pass


def emit_context(message: str) -> None:
    """Add non-blocking information to the model context."""
    response = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": message,
        }
    }
    print(json.dumps(response, ensure_ascii=False))


def emit_feedback(errors: list[str], stop_retrying: bool) -> None:
    """Add validation failures to the model context without hiding tool output."""
    feedback = (
        "Knowledge article JSON validation failed:\n"
        + "\n\n".join(errors)
        + (
            "\nAutomatic retry limit reached. Do not edit the file again "
            "in this turn; report the remaining errors to the user."
            if stop_retrying
            else "\nFix only the remaining errors before continuing."
        )
    )
    response = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": feedback,
        }
    }
    if stop_retrying:
        response.update(
            {
                "continue": False,
                "stopReason": "Knowledge JSON retry limit reached.",
                "systemMessage": feedback,
            }
        )
    print(json.dumps(response, ensure_ascii=False))


def main() -> int:
    """Validate matching article files referenced by the completed tool."""
    event = read_event()
    if event is None:
        return 0

    cwd_value = event.get("cwd")
    cwd = Path(cwd_value) if isinstance(cwd_value, str) else Path.cwd()
    tool_name = event.get("tool_name")
    is_write = (
        isinstance(tool_name, str)
        and tool_name.casefold() in WRITE_TOOL_NAMES
    )
    turn_value = event.get("turn_id")
    turn_id = turn_value if isinstance(turn_value, str) else "unknown-turn"
    tool_input = event.get("tool_input")
    candidates = collect_direct_paths(tool_input)
    candidates.extend(collect_command_paths(tool_input))
    paths = resolve_paths(candidates, cwd.resolve())
    errors: list[str] = []
    fixed_files: list[str] = []
    stop_retrying = False

    for path in paths:
        validation_error = validate_file(path, cwd)
        if validation_error is None:
            clear_failed_writes(path)
            continue

        fixes: list[str] = []
        fix_error: str | None = None
        if is_write:
            fixes, fix_error = apply_safe_fixes(path)
        if fix_error is not None:
            errors.append(fix_error)
        if fixes:
            validation_error = validate_file(path, cwd)
            if validation_error is None:
                clear_failed_writes(path)
                fixed_files.append(f"{path}: {', '.join(fixes)}")
                continue

        attempt_note = ""
        if is_write:
            attempts = record_failed_write(path, turn_id)
            attempt_note = (
                f"\nFailed write attempt {attempts}/"
                f"{MAX_AGENT_FIX_ATTEMPTS}."
            )
            stop_retrying = stop_retrying or (
                attempts >= MAX_AGENT_FIX_ATTEMPTS
            )
        fixes_note = (
            f"\nSafe fixes applied: {', '.join(fixes)}."
            if fixes
            else ""
        )
        errors.append(f"{validation_error}{fixes_note}{attempt_note}")

    if errors:
        emit_feedback(errors, stop_retrying)
    elif fixed_files:
        emit_context(
            "Knowledge article JSON was repaired automatically and now "
            "passes validation:\n" + "\n".join(fixed_files)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
