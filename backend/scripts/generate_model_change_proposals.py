#!/usr/bin/env python3
"""Generate V4.9 self-evolution proposals without applying them."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.evaluation_registry import DEFAULT_DB_PATH, build_evaluation_registry
from app.services.evaluation_registry_repair import build_evaluation_registry_repair_report
from app.services.model_change_proposals import (
    build_data_repair_proposal_from_repair_report,
    build_learning_log_weight_proposal,
    build_proposal_from_experiment,
    build_registry_feature_rule_proposal,
    persist_model_change_proposal,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate proposal-only self-evolution records")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--min-active-logs", type=int, default=30)
    parser.add_argument(
        "--experiment-batch-json",
        default="",
        help="Optional run_accuracy_experiments.py JSON output to convert into proposals",
    )
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    registry = build_evaluation_registry(args.db_path)
    repair_report = build_evaluation_registry_repair_report(args.db_path)
    proposals = [
        build_learning_log_weight_proposal(
            args.db_path,
            sample_registry_hash=registry["registry_hash"],
            min_active_logs=args.min_active_logs,
        ),
        build_registry_feature_rule_proposal(registry),
        build_data_repair_proposal_from_repair_report(repair_report),
    ]
    if args.experiment_batch_json:
        batch = json.loads(Path(args.experiment_batch_json).read_text(encoding="utf-8"))
        proposals.extend(build_proposal_from_experiment(item) for item in batch.get("results", []))
    persisted = (
        [persist_model_change_proposal(args.db_path, proposal) for proposal in proposals]
        if args.persist
        else None
    )
    payload = {
        "schema_version": "model_change_proposal_batch.v1",
        "proposal": proposals[0].to_dict(),
        "proposals": [proposal.to_dict() for proposal in proposals],
        "persisted": persisted,
        "notes": "Proposal-only; production weights and artifacts were not modified.",
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
