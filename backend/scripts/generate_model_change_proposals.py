#!/usr/bin/env python3
"""Generate V4.8 self-evolution proposals without applying them."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.evaluation_registry import DEFAULT_DB_PATH, build_evaluation_registry
from app.services.model_change_proposals import (
    build_learning_log_weight_proposal,
    persist_model_change_proposal,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate proposal-only self-evolution records")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--min-active-logs", type=int, default=30)
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    registry = build_evaluation_registry(args.db_path)
    proposal = build_learning_log_weight_proposal(
        args.db_path,
        sample_registry_hash=registry["registry_hash"],
        min_active_logs=args.min_active_logs,
    )
    payload = {
        "schema_version": "model_change_proposal_batch.v1",
        "proposal": proposal.to_dict(),
        "persisted": persist_model_change_proposal(args.db_path, proposal) if args.persist else None,
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
