#!/usr/bin/env python3
"""Run read-only preflight checks for V4.9 shadow accuracy experiments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.accuracy_experiment_preflight import run_accuracy_experiment_preflight  # noqa: E402
from app.services.evaluation_registry import DEFAULT_DB_PATH, WC26_COMPETITION  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight shadow accuracy experiments")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--competition", default=WC26_COMPETITION)
    parser.add_argument("--min-sample-count", type=int, default=30)
    parser.add_argument("--candidates", default="")
    parser.add_argument("--output", default="", help="Optional JSON output path")
    args = parser.parse_args()

    candidates = [item.strip() for item in args.candidates.split(",") if item.strip()]
    payload = run_accuracy_experiment_preflight(
        args.db_path,
        competition=args.competition,
        min_sample_count=args.min_sample_count,
        candidates=candidates,
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
