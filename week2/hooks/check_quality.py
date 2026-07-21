#!/usr/bin/env python3
"""Score the quality of one or more knowledge-entry JSON files."""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


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

STANDARD_TAGS = {
    "ai",
    "algorithm",
    "api",
    "architecture",
    "backend",
    "big-data",
    "cloud",
    "database",
    "data-science",
    "deep-learning",
    "devops",
    "docker",
    "frontend",
    "github",
    "go",
    "java",
    "javascript",
    "kubernetes",
    "llm",
    "machine-learning",
    "open-source",
    "python",
    "rust",
    "security",
    "testing",
    "typescript",
    "人工智能",
    "云计算",
    "前端",
    "后端",
    "大数据",
    "安全",
    "开源",
    "数据库",
    "机器学习",
    "测试",
    "算法",
    "架构",
}

TECHNICAL_KEYWORDS = {
    "ai",
    "algorithm",
    "api",
    "architecture",
    "cloud",
    "database",
    "docker",
    "framework",
    "kubernetes",
    "llm",
    "model",
    "python",
    "人工智能",
    "云计算",
    "接口",
    "数据库",
    "模型",
    "算法",
    "架构",
}

CHINESE_EMPTY_WORDS = {
    "赋能",
    "抓手",
    "闭环",
    "打通",
    "全链路",
    "底层逻辑",
    "颗粒度",
    "对齐",
    "拉通",
    "沉淀",
    "强大的",
    "革命性的",
}

ENGLISH_EMPTY_WORDS = {
    "cutting-edge",
    "disruptive",
    "game-changing",
    "groundbreaking",
    "next-generation",
    "revolutionary",
    "synergy",
    "transformative",
}


@dataclass
class DimensionScore:
    """The result of one quality dimension."""

    name: str
    score: float
    max_score: int
    notes: list[str] = field(default_factory=list)


@dataclass
class QualityReport:
    """The complete quality report for one JSON file."""

    path: Path
    dimensions: list[DimensionScore] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def total_score(self) -> float:
        """Return the sum of all dimension scores."""
        return round(sum(item.score for item in self.dimensions), 1)

    @property
    def grade(self) -> str:
        """Convert the total score to grade A, B, or C."""
        if self.errors or self.total_score < 60:
            return "C"
        if self.total_score < 80:
            return "B"
        return "A"


def expand_paths(patterns: list[str]) -> tuple[list[Path], list[str]]:
    """Expand paths and glob patterns while removing duplicates."""
    paths: list[Path] = []
    errors: list[str] = []
    seen: set[Path] = set()

    for pattern in patterns:
        matches = [Path(match) for match in sorted(glob.glob(pattern))]
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


def contains_keyword(text: str, keyword: str) -> bool:
    """Match English keywords as terms and Chinese keywords as substrings."""
    if keyword.isascii():
        pattern = rf"(?<![A-Za-z0-9]){re.escape(keyword)}(?![A-Za-z0-9])"
        return re.search(pattern, text, re.IGNORECASE) is not None
    return keyword in text


def score_summary(data: dict[str, Any]) -> DimensionScore:
    """Score summary length and technical specificity."""
    summary = data.get("summary")
    if not isinstance(summary, str):
        return DimensionScore("摘要质量", 0.0, 25, ["summary 缺失或不是字符串"])

    length = len(re.sub(r"\s+", "", summary))
    keywords = sorted(
        keyword
        for keyword in TECHNICAL_KEYWORDS
        if contains_keyword(summary, keyword)
    )

    if length >= 50:
        score = 25.0
    elif length >= 20:
        score = min(25.0, 15.0 + len(keywords) * 2.0)
    else:
        score = round(15.0 * length / 20, 1)

    notes = [f"摘要长度 {length} 字符"]
    if keywords:
        notes.append(f"技术关键词：{', '.join(keywords)}")
    elif length < 50:
        notes.append("未发现技术关键词")
    return DimensionScore("摘要质量", score, 25, notes)


def score_technical_depth(data: dict[str, Any]) -> DimensionScore:
    """Map the article score range 0-1 linearly onto 0-25."""
    article_score = data.get("score")
    if (
        isinstance(article_score, bool)
        or not isinstance(article_score, (int, float))
        or not 0 <= article_score <= 1
    ):
        return DimensionScore(
            "技术深度",
            0.0,
            25,
            ["score 缺失或不在 0-1 范围内"],
        )

    score = round(float(article_score) * 25.0, 1)
    return DimensionScore(
        "技术深度",
        score,
        25,
        [f"文章 score：{article_score}"],
    )


def is_valid_timestamp(value: Any) -> bool:
    """Return whether value is an ISO 8601 timestamp."""
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def score_format(data: dict[str, Any]) -> DimensionScore:
    """Award four points for each valid format field."""
    entry_id = data.get("id")
    status = data.get("status")
    checks = {
        "id": type(entry_id) is int and entry_id > 0,
        "title": isinstance(data.get("title"), str)
        and bool(data["title"].strip()),
        "source_url": isinstance(data.get("source_url"), str)
        and bool(URL_PATTERN.fullmatch(data["source_url"])),
        "status": isinstance(status, str) and status in VALID_STATUSES,
        "timestamp": is_valid_timestamp(data.get("timestamp")),
    }
    valid_fields = [name for name, valid in checks.items() if valid]
    invalid_fields = [name for name, valid in checks.items() if not valid]
    notes = [f"有效字段：{', '.join(valid_fields) or '无'}"]
    if invalid_fields:
        notes.append(f"缺失或格式错误：{', '.join(invalid_fields)}")
    return DimensionScore("格式规范", len(valid_fields) * 4.0, 20, notes)


def score_tags(data: dict[str, Any]) -> DimensionScore:
    """Score tag count and conformance with the standard tag list."""
    tags = data.get("tags")
    if not isinstance(tags, list) or not tags:
        return DimensionScore("标签精度", 0.0, 15, ["tags 缺失或为空"])

    normalized = [tag.casefold() for tag in tags if isinstance(tag, str)]
    valid_tags = [tag for tag in normalized if tag in STANDARD_TAGS]
    invalid_tags = [tag for tag in normalized if tag not in STANDARD_TAGS]
    invalid_count = len(tags) - len(normalized)

    count_score = 6.0 if 1 <= len(tags) <= 3 else 3.0
    validity_score = 9.0 * len(valid_tags) / len(tags)
    score = round(count_score + validity_score, 1)

    notes = [f"标签数量 {len(tags)}，合法标签 {len(valid_tags)}"]
    if invalid_tags:
        notes.append(f"非标准标签：{', '.join(invalid_tags)}")
    if invalid_count:
        notes.append(f"{invalid_count} 个标签不是字符串")
    if len(tags) > 3:
        notes.append("建议保留 1-3 个标签")
    return DimensionScore("标签精度", score, 15, notes)


def score_empty_words(data: dict[str, Any]) -> DimensionScore:
    """Deduct points for vague Chinese and English marketing words."""
    text = " ".join(
        value for value in (data.get("title"), data.get("summary"))
        if isinstance(value, str)
    )
    found_chinese = sorted(word for word in CHINESE_EMPTY_WORDS if word in text)
    found_english = sorted(
        word for word in ENGLISH_EMPTY_WORDS if contains_keyword(text, word)
    )
    found = found_chinese + found_english
    score = float(max(0, 15 - 3 * len(found)))
    notes = (
        [f"发现空洞词：{', '.join(found)}"]
        if found
        else ["未发现空洞词"]
    )
    return DimensionScore("空洞词检测", score, 15, notes)


def build_report(path: Path) -> QualityReport:
    """Load a file and build its quality report."""
    if not path.is_file():
        return QualityReport(path, errors=["不是普通文件"])

    try:
        with path.open("r", encoding="utf-8") as json_file:
            data = json.load(json_file)
    except (OSError, UnicodeError) as error:
        return QualityReport(path, errors=[f"无法读取文件：{error}"])
    except json.JSONDecodeError as error:
        message = f"JSON 解析失败（第 {error.lineno} 行，第 {error.colno} 列）"
        return QualityReport(path, errors=[f"{message}：{error.msg}"])

    if not isinstance(data, dict):
        return QualityReport(path, errors=["JSON 顶层必须是对象"])

    dimensions = [
        score_summary(data),
        score_technical_depth(data),
        score_format(data),
        score_tags(data),
        score_empty_words(data),
    ]
    return QualityReport(path, dimensions)


def progress_bar(score: float, maximum: int, width: int = 20) -> str:
    """Create a fixed-width ASCII progress bar."""
    ratio = min(max(score / maximum, 0.0), 1.0) if maximum else 0.0
    filled = round(ratio * width)
    return f"[{'#' * filled}{'-' * (width - filled)}]"


def print_report(report: QualityReport) -> None:
    """Print a quality report to standard output."""
    print(f"\n{report.path}")
    if report.errors:
        print(f"  {progress_bar(0, 100)} 0.0/100  等级 C")
        for error in report.errors:
            print(f"  ERROR: {error}")
        return

    print(
        f"  {progress_bar(report.total_score, 100)} "
        f"{report.total_score:.1f}/100  等级 {report.grade}"
    )
    for dimension in report.dimensions:
        print(
            f"  {dimension.name:<8} "
            f"{progress_bar(dimension.score, dimension.max_score, 10)} "
            f"{dimension.score:>4.1f}/{dimension.max_score}"
        )
        for note in dimension.notes:
            print(f"    - {note}")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Score knowledge-entry JSON files on five dimensions."
    )
    parser.add_argument(
        "json_files",
        nargs="+",
        metavar="json_file",
        help="one or more JSON files or glob patterns",
    )
    return parser.parse_args()


def main() -> int:
    """Score all input files and return one when any report is grade C."""
    args = parse_args()
    paths, input_errors = expand_paths(args.json_files)
    reports = [build_report(path) for path in paths]

    for error in input_errors:
        print(f"ERROR: {error}", file=sys.stderr)
    for report in reports:
        print_report(report)

    grade_counts = {
        grade: sum(report.grade == grade for report in reports)
        for grade in ("A", "B", "C")
    }
    print(
        f"\n汇总：{len(reports)} 个文件，"
        f"A {grade_counts['A']}，B {grade_counts['B']}，"
        f"C {grade_counts['C']}，输入错误 {len(input_errors)}"
    )

    return 1 if input_errors or grade_counts["C"] else 0


if __name__ == "__main__":
    sys.exit(main())
