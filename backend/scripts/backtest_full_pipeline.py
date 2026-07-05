#!/usr/bin/env python3
"""Deprecated compatibility wrapper for the unified accuracy runner.

Use ``run_accuracy_experiments.py`` for new work.  This wrapper keeps the old
command name available while preventing legacy half-life scripts from writing
historical config or model artifacts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.evaluation_registry import DEFAULT_DB_PATH
from _accuracy_wrapper import run_accuracy_wrapper


DEFAULT_CANDIDATES = [
    "dynamic_dixon_coles",
    "dynamic_bivariate_poisson",
    "dynamic_bayesian_weighted_goal_model",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Deprecated wrapper for unified shadow accuracy experiments")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--competition", default="FIFA World Cup 2026")
    parser.add_argument("--min-sample-count", type=int, default=30)
    parser.add_argument("--candidates", default=",".join(DEFAULT_CANDIDATES))
    parser.add_argument("--output", default="")
    parser.add_argument("--persist", action="store_true", help="Persist audit rows only")
    parser.add_argument("--halflife", default="", help="Deprecated; ignored by the unified runner")
    args = parser.parse_args()

    candidates = [item.strip() for item in args.candidates.split(",") if item.strip()]
    return run_accuracy_wrapper(
        db_path=args.db_path,
        competition=args.competition,
        candidates=candidates,
        min_sample_count=args.min_sample_count,
        output=args.output,
        persist=args.persist,
        wrapper_name="backtest_full_pipeline.py",
        deprecated_args={"halflife": args.halflife},
    )


if __name__ == "__main__":
    raise SystemExit(main())
