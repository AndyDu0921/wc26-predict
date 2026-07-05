#!/usr/bin/env python3
"""Materialize V4.9 feature snapshots from the evaluation registry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.evaluation_registry import DEFAULT_DB_PATH
from app.services.feature_snapshot_materializer import (
    build_feature_snapshot_records,
    persist_feature_snapshot_records,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize pre-result feature snapshots")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--competition", default="FIFA World Cup 2026")
    parser.add_argument("--sample-status", default="strict", choices=("strict", "diagnostic", "rejected", "all"))
    parser.add_argument("--persist", action="store_true", help="Write feature_snapshots audit rows")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    records = build_feature_snapshot_records(
        args.db_path,
        sample_status=args.sample_status,
        competition=args.competition,
    )
    persisted = persist_feature_snapshot_records(args.db_path, records) if args.persist else None
    payload = {
        "schema_version": "feature_snapshot_materialization.v1",
        "db_path": args.db_path,
        "competition": args.competition,
        "sample_status": args.sample_status,
        "records_built": len(records),
        "persisted": persisted,
        "notes": "Feature snapshots exclude actual goals and do not mutate production weights or artifacts.",
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
