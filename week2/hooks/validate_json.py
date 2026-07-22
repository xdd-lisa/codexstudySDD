#!/usr/bin/env python3
"""Validate one or more knowledge-entry JSON files."""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from pathlib import Path
from typing import Any


REQUIRED_FIELDS: dict[str, type] = {
    "id": int,
    "title": str,
    "source_url": str,
    "summary": str,
    "tags": list,
    "status": str,
}

URL_PATTERN = re.compile(r"^https?://\S+$", re.IGNORECASE)
VALID_STATUSES = {
    "draft",
    "collected",
    "analyzed",
    "ready",
    "published",
    "rejected",
    "failed",
}
VALID_AUDIENCES = {"beginner", "intermediate", "advanced"}


def expand_paths(patterns: list[str]) -> tuple[list[Path], list[str]]:
    """Expand input paths and glob patterns, preserving their input order."""
    paths: list[Path] = []
    errors: list[str] = []
    seen: set[Path] = set()

    for pattern in patterns:
        matches = [Path(match) for match in glob.glob(pattern)]
        if not matches:
            candidate = Path(pattern)
            if candidate.exists():
                matches = [candidate]
            else:
                errors.append(f"{pattern}: file or pattern did not match")
                continue

        for path in matches:
            if path not in seen:
                paths.append(path)
                seen.add(path)

    return paths, errors


def validate_required_fields(data: dict[str, Any]) -> list[str]:
    """Validate that required fields exist and have the expected types."""
    errors: list[str] = []

    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in data:
            errors.append(f"missing required field: {field}")
        elif type(data[field]) is not expected_type:
            errors.append(
                f"field '{field}' must be {expected_type.__name__}, "
                f"got {type(data[field]).__name__}"
            )

    return errors


def validate_entry(data: dict[str, Any]) -> list[str]:
    """Validate the fields and business rules of one knowledge entry."""
    errors = validate_required_fields(data)

    entry_id = data.get("id")
    if type(entry_id) is int and entry_id <= 0:
        errors.append("field 'id' must be a positive integer")

    status = data.get("status")
    if isinstance(status, str) and status not in VALID_STATUSES:
        allowed = ", ".join(sorted(VALID_STATUSES))
        errors.append(f"field 'status' must be one of: {allowed}")

    source_url = data.get("source_url")
    if isinstance(source_url, str) and not URL_PATTERN.fullmatch(source_url):
        errors.append("field 'source_url' must be a valid HTTP(S) URL")

    summary = data.get("summary")
    if isinstance(summary, str) and len(summary.strip()) < 20:
        errors.append("field 'summary' must contain at least 20 characters")

    tags = data.get("tags")
    if isinstance(tags, list) and not tags:
        errors.append("field 'tags' must contain at least one item")

    if "score" in data:
        score = data["score"]
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not 0 <= score <= 1
        ):
            errors.append("field 'score' must be a number between 0 and 1")

    if "audience" in data:
        audience = data["audience"]
        if not isinstance(audience, str) or audience not in VALID_AUDIENCES:
            allowed = ", ".join(sorted(VALID_AUDIENCES))
            errors.append(f"field 'audience' must be one of: {allowed}")

    return errors


def validate_file(path: Path) -> list[str]:
    """Parse and validate a single JSON file."""
    if not path.is_file():
        return ["not a regular file"]

    try:
        with path.open("r", encoding="utf-8") as json_file:
            data = json.load(json_file)
    except (OSError, UnicodeError) as error:
        return [f"could not read file: {error}"]
    except json.JSONDecodeError as error:
        return [
            "invalid JSON at "
            f"line {error.lineno}, column {error.colno}: {error.msg}"
        ]

    if not isinstance(data, dict):
        return ["top-level JSON value must be an object"]

    return validate_entry(data)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Validate knowledge-entry JSON files."
    )
    parser.add_argument(
        "json_files",
        nargs="+",
        metavar="json_file",
        help="one or more JSON files or glob patterns",
    )
    return parser.parse_args()


def main() -> int:
    """Run validation and return a process exit code."""
    args = parse_args()
    paths, input_errors = expand_paths(args.json_files)
    results: list[tuple[Path, list[str]]] = []

    for path in paths:
        results.append((path, validate_file(path)))

    failed_files = sum(bool(errors) for _, errors in results)
    total_errors = len(input_errors) + sum(
        len(errors) for _, errors in results
    )

    for error in input_errors:
        print(f"ERROR: {error}", file=sys.stderr)

    for path, errors in results:
        if errors:
            print(f"FAIL: {path}", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
        else:
            print(f"PASS: {path}")

    print(
        f"Summary: {len(paths)} file(s), "
        f"{len(paths) - failed_files} passed, "
        f"{failed_files} failed, {total_errors} error(s)"
    )

    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
