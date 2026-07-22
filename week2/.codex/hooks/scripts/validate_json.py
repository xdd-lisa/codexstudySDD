#!/usr/bin/env python3
"""Validate article files against the pipeline's canonical contract."""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from knowledge_base.schema import validate_article  # noqa: E402


def expand_paths(patterns: list[str]) -> tuple[list[Path], list[str]]:
    paths: list[Path] = []
    errors: list[str] = []
    for pattern in patterns:
        matches = [Path(value) for value in glob.glob(pattern)]
        if not matches and Path(pattern).exists():
            matches = [Path(pattern)]
        if not matches:
            errors.append(f"{pattern}: file or pattern did not match")
        for path in matches:
            if path not in paths:
                paths.append(path)
    return paths, errors


def validate_file(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return [f"could not read valid JSON: {error}"]
    if not isinstance(payload, dict):
        return ["top-level JSON value must be an object"]
    return validate_article(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_files", nargs="+")
    args = parser.parse_args()
    paths, input_errors = expand_paths(args.json_files)
    failures = 0
    for error in input_errors:
        print(f"ERROR: {error}", file=sys.stderr)
    for path in paths:
        errors = validate_file(path)
        if errors:
            failures += 1
            print(f"FAIL: {path}", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
        else:
            print(f"PASS: {path}")
    print(f"Summary: {len(paths)} file(s), {len(paths) - failures} passed, {failures} failed, {len(input_errors) + failures} error(s)")
    return 1 if input_errors or failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
