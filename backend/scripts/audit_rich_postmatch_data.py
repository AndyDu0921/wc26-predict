#!/usr/bin/env python3
"""Audit whether a match has rich post-match data for V4.11 reviews."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.evaluation_registry import DEFAULT_DB_PATH
from app.services.match_data.rich_context import PASSING_TIERS, load_rich_postmatch_context


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match-id", required=True)
    parser.add_argument("--home-team", default=None)
    parser.add_argument("--away-team", default=None)
    parser.add_argument("--home-score", type=int, default=None)
    parser.add_argument("--away-score", type=int, default=None)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = load_rich_postmatch_context(
        args.db_path,
        match_id=args.match_id,
        home_team=args.home_team,
        away_team=args.away_team,
        home_score=args.home_score,
        away_score=args.away_score,
    )
    result["passed"] = result["tier"] in PASSING_TIERS
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return
    print(f"Match: {args.match_id}")
    print(f"Tier: {result['tier']}")
    print(f"Available: {result['available']}")
    print(f"Event quality: {result['event_quality_score']:.4f}")
    print(f"Counts: {result['counts']}")
    print(f"Coverage: {result.get('coverage', {})}")
    print(f"Warnings: {', '.join(result.get('warnings') or []) if result.get('warnings') else 'none'}")
    print(f"Missing: {', '.join(result['missing']) if result['missing'] else 'none'}")
    print(f"Comeback: {result['comeback_profile']}")
    raise SystemExit(0 if result["passed"] else 2)


if __name__ == "__main__":
    main()

