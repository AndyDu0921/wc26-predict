#!/usr/bin/env python3
"""Deprecated compatibility wrapper for stacking candidate experiments.

The old script wrote stacking/conformal artifacts directly.  V4.9 keeps
stacking as a shadow candidate until strict samples and gate evidence are
sufficient, so this wrapper delegates to the unified accuracy runner.
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
    "proper_scoring_stacking_candidate",
    "dirichlet_calibration_candidate",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Deprecated wrapper for stacking/calibration shadow experiments")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--competition", default="FIFA World Cup 2026")
    parser.add_argument("--min-sample-count", type=int, default=30)
    parser.add_argument("--candidates", default=",".join(DEFAULT_CANDIDATES))
    parser.add_argument("--output", default="")
    parser.add_argument("--persist", action="store_true", help="Persist audit rows only")
    parser.add_argument("--skip-training", action="store_true", help="Deprecated; training is always disabled here")
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
        wrapper_name="collect_stacking_training_data.py",
        deprecated_args={"skip_training": args.skip_training, "halflife": args.halflife},
    )


if __name__ == "__main__":
    raise SystemExit(main())
