#!/usr/bin/env python3
"""Normalize raw official match data into event, lineup, shot, and player tables."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.services.evaluation_registry import DEFAULT_DB_PATH
from app.services.match_data.normalizer import normalize_official_payload
from app.services.match_data.storage import load_latest_raw_payload, save_normalized_match_data


def _payload_for_normalization(raw_row: dict) -> dict:
    payload = raw_row["payload"]
    structured = payload.get("structured_payloads") if isinstance(payload, dict) else None
    if isinstance(structured, list) and structured:
        return {
            "structured_payloads": structured,
            "report_links": payload.get("discovered_links", []),
        }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match-id", required=True)
    parser.add_argument("--home-team", default=None)
    parser.add_argument("--away-team", default=None)
    parser.add_argument("--provider", default="fifa_official")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-replace-existing", action="store_true")
    args = parser.parse_args()

    raw = load_latest_raw_payload(args.db_path, args.match_id, provider=args.provider)
    if raw is None:
        raise SystemExit(f"No raw match data found for match_id={args.match_id} provider={args.provider}")

    normalized = normalize_official_payload(
        _payload_for_normalization(raw),
        match_id=args.match_id,
        provider=args.provider,
        home_team=args.home_team,
        away_team=args.away_team,
    )
    summary = {
        "match_id": args.match_id,
        "provider": args.provider,
        "status": normalized.status,
        "warnings": normalized.warnings,
        "events": len(normalized.events),
        "shots": len(normalized.shots),
        "lineups": len(normalized.lineups),
        "player_stats": len(normalized.player_stats),
        "dry_run": args.dry_run,
    }
    if not args.dry_run:
        summary["storage"] = save_normalized_match_data(
            args.db_path,
            normalized,
            replace_existing=not args.no_replace_existing,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

