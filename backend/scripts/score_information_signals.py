#!/usr/bin/env python3
"""Score V4.10 information-state signals as shadow adjustments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.evaluation_registry import DEFAULT_DB_PATH  # noqa: E402
from app.services.information_state_engine import score_information_signals  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Score V4.10 information-state signals")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--match-id", default="")
    parser.add_argument("--home", default="")
    parser.add_argument("--away", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    payload = score_information_signals(
        args.db_path,
        match_id=args.match_id or None,
        home_team=args.home or None,
        away_team=args.away or None,
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
