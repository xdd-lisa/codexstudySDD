#!/usr/bin/env python3
"""Score canonical article JSON without redefining its schema."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from knowledge_base.schema import validate_article  # noqa: E402


def score_article(article: dict[str, object]) -> tuple[float, list[str]]:
    errors = validate_article(article)
    if errors:
        return 0.0, errors
    summary = str(article["summary"])
    tags = article["tags"]
    score = float(article["score"])
    summary_points = min(30.0, 15.0 + len(summary.strip()) / 4)
    depth_points = score * 3.0
    tag_points = min(20.0, len(tags) * 5.0) if isinstance(tags, list) else 0.0
    format_points = 20.0
    return round(min(100.0, summary_points + depth_points + tag_points + format_points), 1), []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_files", nargs="+")
    args = parser.parse_args()
    failed = 0
    for value in args.json_files:
        path = Path(value)
        try:
            article = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"FAIL: {path}: {error}", file=sys.stderr)
            failed += 1
            continue
        quality, errors = score_article(article) if isinstance(article, dict) else (0.0, ["top-level JSON must be an object"])
        if errors:
            failed += 1
            print(f"FAIL: {path}: {'; '.join(errors)}", file=sys.stderr)
        else:
            grade = "A" if quality >= 80 else "B" if quality >= 60 else "C"
            print(f"{path}: {quality:.1f}/100 grade {grade}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
