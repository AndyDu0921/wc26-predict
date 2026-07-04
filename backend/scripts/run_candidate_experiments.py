#!/usr/bin/env python3
"""Run shadow candidate experiments without changing production state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.candidate_experiments import CandidateExperimentConfig, run_candidate_experiment
from app.services.evaluation_registry import DEFAULT_DB_PATH


def main() -> int:
    parser = argparse.ArgumentParser(description="Run paired shadow candidate experiment")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--candidate", default="uniform_baseline")
    parser.add_argument("--min-sample-count", type=int, default=30)
    parser.add_argument("--competition", default="FIFA World Cup 2026")
    parser.add_argument("--output", default="", help="Optional JSON output path")
    args = parser.parse_args()

    result = run_candidate_experiment(
        args.db_path,
        config=CandidateExperimentConfig(
            candidate_name=args.candidate,
            min_sample_count=args.min_sample_count,
            competition=args.competition,
        ),
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0 if result.get("status") == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
