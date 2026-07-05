#!/usr/bin/env python3
"""Build the V4.9 read-only evaluation registry repair report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.evaluation_registry import DEFAULT_DB_PATH
from app.services.evaluation_registry_repair import build_evaluation_registry_repair_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build read-only evaluation registry repair report")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--competition", default="FIFA World Cup 2026")
    parser.add_argument("--include-strict", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    payload = build_evaluation_registry_repair_report(
        args.db_path,
        competition=args.competition,
        include_strict=args.include_strict,
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
