#!/usr/bin/env python3
"""Build the read-only WC26 evaluation registry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.evaluation_registry import DEFAULT_DB_PATH, build_evaluation_registry


def main() -> int:
    parser = argparse.ArgumentParser(description="Build leak-aware evaluation registry")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--competition", default="FIFA World Cup 2026")
    parser.add_argument("--output", default="", help="Optional JSON output path")
    args = parser.parse_args()

    registry = build_evaluation_registry(args.db_path, competition=args.competition)
    payload = json.dumps(registry, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
