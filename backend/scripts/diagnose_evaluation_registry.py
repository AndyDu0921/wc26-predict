#!/usr/bin/env python3
"""Diagnose V4.9 evaluation sample quality."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.evaluation_registry import DEFAULT_DB_PATH, build_evaluation_registry


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose evaluation registry sample quality")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--competition", default="FIFA World Cup 2026")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    registry = build_evaluation_registry(args.db_path, competition=args.competition)
    rows = registry["samples"]
    status_counts = Counter(row["sample_status"] for row in rows)
    leakage_counts = Counter(row["leakage_status"] for row in rows)
    reason_counts = Counter(
        reason
        for row in rows
        for reason in row.get("exclusion_reasons", [])
    )
    horizon_counts = Counter(row["horizon_bucket"] for row in rows)

    payload = {
        "schema_version": "evaluation_registry_diagnostics.v1",
        "registry_hash": registry["registry_hash"],
        "summary": registry["summary"],
        "sample_status_counts": dict(status_counts),
        "leakage_status_counts": dict(leakage_counts),
        "horizon_bucket_counts": dict(horizon_counts),
        "top_exclusion_reasons": dict(reason_counts.most_common(20)),
        "notes": "Read-only diagnostics; no source data was modified.",
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
