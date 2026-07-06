#!/usr/bin/env python3
"""Build a read-only strict-sample repair queue."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.evaluation_registry import DEFAULT_DB_PATH, WC26_COMPETITION  # noqa: E402
from app.services.strict_sample_repair_queue import build_strict_sample_repair_queue  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build read-only strict sample repair queue")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--competition", default=WC26_COMPETITION)
    parser.add_argument("--include-strict", action="store_true")
    parser.add_argument("--output", default="", help="Optional JSON output path")
    args = parser.parse_args()

    payload = build_strict_sample_repair_queue(
        args.db_path,
        competition=args.competition,
        include_strict=args.include_strict,
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
