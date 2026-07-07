#!/usr/bin/env python3
"""Build the V4.9 read-only accuracy todo backlog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.accuracy_todo_backlog import (  # noqa: E402
    STRICT_SAMPLE_TARGET,
    build_accuracy_todo_backlog,
)
from app.services.evaluation_registry import DEFAULT_DB_PATH, WC26_COMPETITION  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build read-only V4.9 accuracy todo backlog")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--competition", default=WC26_COMPETITION)
    parser.add_argument("--strict-target", type=int, default=STRICT_SAMPLE_TARGET)
    parser.add_argument("--output", default="", help="Optional JSON output path")
    args = parser.parse_args()

    payload = build_accuracy_todo_backlog(
        args.db_path,
        competition=args.competition,
        strict_sample_target=args.strict_target,
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
