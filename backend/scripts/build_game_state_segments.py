#!/usr/bin/env python3
"""Build game-state segments and comeback profile from normalized events."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.evaluation_registry import DEFAULT_DB_PATH
from app.services.match_data.game_state import build_game_state_profile
from app.services.match_data.rich_context import _load_events, _load_shots
from app.services.match_data.storage import ensure_match_data_os_tables, save_game_state_segments


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match-id", required=True)
    parser.add_argument("--home-score", type=int, default=None)
    parser.add_argument("--away-score", type=int, default=None)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ensure_match_data_os_tables(args.db_path)
    events = _load_events(args.db_path, args.match_id)
    shots = _load_shots(args.db_path, args.match_id)
    profile = build_game_state_profile(
        match_id=args.match_id,
        events=events,
        shots=shots,
        final_home_goals=args.home_score,
        final_away_goals=args.away_score,
    )
    summary = {
        "match_id": args.match_id,
        "events": len(events),
        "shots": len(shots),
        "segments": len(profile["segments"]),
        "event_quality_score": profile["event_quality_score"],
        "comeback_profile": profile["comeback_profile"],
        "dry_run": args.dry_run,
    }
    if not args.dry_run:
        summary["stored_segments"] = save_game_state_segments(
            args.db_path,
            args.match_id,
            profile["segments"],
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

