#!/usr/bin/env python3
"""Deprecated compatibility wrapper for score-model candidate experiments.

The old grid search read post-match xG fallbacks and printed parameter
recommendations outside the proposal gate.  New work must use the unified
shadow experiment runner and promote changes only through proposals.
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
    parser = argparse.ArgumentParser(description="Deprecated wrapper for score-model shadow experiments")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--competition", default="FIFA World Cup 2026")
    parser.add_argument("--min-sample-count", type=int, default=30)
    parser.add_argument("--candidates", default=",".join(DEFAULT_CANDIDATES))
    parser.add_argument("--output", default="")
    parser.add_argument("--persist", action="store_true", help="Persist audit rows only")
    parser.add_argument("--top-n", default="", help="Deprecated; ignored by the unified runner")
    args = parser.parse_args()

    candidates = [item.strip() for item in args.candidates.split(",") if item.strip()]
    return run_accuracy_wrapper(
        db_path=args.db_path,
        competition=args.competition,
        candidates=candidates,
        min_sample_count=args.min_sample_count,
        output=args.output,
        persist=args.persist,
        wrapper_name="grid_search_score_params.py",
        deprecated_args={"top_n": args.top_n},
    )


if __name__ == "__main__":
    raise SystemExit(main())
