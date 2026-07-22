"""Atomic filesystem storage, failure isolation, and checkpoint persistence."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"
RAW_DIR = KNOWLEDGE_DIR / "raw"
FAILED_DIR = KNOWLEDGE_DIR / "failed"
CHECKPOINT_PATH = KNOWLEDGE_DIR / "checkpoint.json"


def save_raw(items: Sequence[Mapping[str, Any]], dry_run: bool = False) -> Path | None:
    path = next_raw_path()
    if dry_run:
        return None
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, list(items))
    return path


def next_raw_path(now: datetime | None = None) -> Path:
    current = now or datetime.now(UTC)
    date_part = current.astimezone(UTC).strftime("%Y%m%d")
    pattern = re.compile(rf"^raw_{date_part}_(\d{{3,}})\.json$")
    sequences = [
        int(match.group(1))
        for path in RAW_DIR.glob(f"raw_{date_part}_*.json")
        if (match := pattern.fullmatch(path.name))
    ]
    return RAW_DIR / f"raw_{date_part}_{max(sequences, default=0) + 1:03d}.json"


def load_checkpoint(path: Path = CHECKPOINT_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "completed": {}, "failed": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid checkpoint {path}: {error}") from error
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError(f"unsupported checkpoint format: {path}")
    value.setdefault("completed", {})
    value.setdefault("failed", {})
    return value


def save_checkpoint(checkpoint: Mapping[str, Any], path: Path = CHECKPOINT_PATH) -> None:
    payload = dict(checkpoint)
    payload["version"] = 1
    payload["updated_at"] = utc_now()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, payload)


def record_failure(failure: Mapping[str, Any], directory: Path = FAILED_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    identity = str(failure.get("external_id") or failure.get("source") or failure.get("error"))
    from .collector import short_hash

    path = (
        directory / f"{slugify(str(failure.get('stage', 'unknown')))}-{short_hash(identity)}.json"
    )
    payload = dict(failure)
    payload["occurred_at"] = utc_now()
    write_json_atomic(path, payload)
    return path


def write_json_atomic(path: Path, payload: Any) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:60] or "failure"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
