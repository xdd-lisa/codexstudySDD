#!/usr/bin/env python3
"""Thin project-local launcher for the local-knowledge MCP server."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from server import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
