#!/usr/bin/env python3
"""Run V4.9 shadow accuracy experiments.

Default behavior is read-only: JSON is printed or written to --output.
Use --persist to write only audit tables (experiment_runs and
candidate_predictions); production weights and artifacts are never changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.accuracy_experiment_store import persist_experiment_result
from app.services.candidate_experiments import CandidateExperimentConfig, run_candidate_experiment
from app.services.evaluation_registry import DEFAULT_DB_PATH
from app.services.shadow_candidate_models import SUPPORTED_SHADOW_CANDIDATES


DEFAULT_CANDIDATES = (
    "current_fusion",
    "uniform_baseline",
    "dynamic_dixon_coles",
    "dynamic_bivariate_poisson",
    "dynamic_bayesian_weighted_goal_model",
    "international_covariate_hybrid",
    "dirichlet_calibration_candidate",
    "proper_scoring_stacking_candidate",
    "player_availability_shadow",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run V4.9 shadow accuracy experiments")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--competition", default="FIFA World Cup 2026")
    parser.add_argument("--candidates", default=",".join(DEFAULT_CANDIDATES))
    parser.add_argument("--min-sample-count", type=int, default=30)
    parser.add_argument("--output", default="", help="Optional JSON output path")
    parser.add_argument("--persist", action="store_true", help="Persist audit rows only")
    args = parser.parse_args()

    candidates = [item.strip() for item in args.candidates.split(",") if item.strip()]
    unknown = [item for item in candidates if item not in SUPPORTED_SHADOW_CANDIDATES]
    if unknown:
        raise SystemExit(f"Unsupported candidate(s): {', '.join(unknown)}")

    results = []
    persisted = []
    for candidate_name in candidates:
        result = run_candidate_experiment(
            args.db_path,
            config=CandidateExperimentConfig(
                candidate_name=candidate_name,
                min_sample_count=args.min_sample_count,
                competition=args.competition,
                include_predictions=args.persist,
            ),
        )
        results.append(result)
        if args.persist:
            persisted.append(persist_experiment_result(args.db_path, result))

    payload = {
        "schema_version": "accuracy_experiment_batch.v1",
        "db_path": args.db_path,
        "competition": args.competition,
        "min_sample_count": args.min_sample_count,
        "results": results,
        "persisted": persisted,
        "notes": "Shadow-only batch; production weights and artifacts were not modified.",
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
