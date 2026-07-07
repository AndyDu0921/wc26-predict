#!/usr/bin/env python3
"""Audit or conservatively repair local SQLite integrity drift."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.db_integrity_audit import audit_sqlite_integrity, repair_sqlite_foreign_key_drift
from app.services.evaluation_registry import DEFAULT_DB_PATH


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit SQLite integrity and foreign-key drift")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--apply", action="store_true", help="Apply conservative FK drift repairs")
    parser.add_argument("--no-backup", action="store_true", help="Skip backup in apply mode")
    parser.add_argument("--output", default="", help="Optional JSON output path")
    args = parser.parse_args()

    payload = (
        repair_sqlite_foreign_key_drift(args.db_path, backup=not args.no_backup)
        if args.apply
        else audit_sqlite_integrity(args.db_path)
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
