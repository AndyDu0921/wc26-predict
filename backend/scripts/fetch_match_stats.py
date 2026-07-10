#!/usr/bin/env python
"""
V4.6-process-eval: Fetch match statistics from providers and store in DB.

Usage:
  python backend/scripts/fetch_match_stats.py --match-id 183 [--provider fbref] [--dry-run]
  python backend/scripts/fetch_match_stats.py --match-id 183 --manual-xg 1.80,0.30

Data flow:
  provider.fetch() → RawMatchStats → normalize → TeamMatchStats → DB tables
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "local_stage2.db"

# Allow running from repo root without installing the package
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def get_match_info(db_path: Path, match_id: int) -> Optional[Dict]:
    """Get home_team, away_team, match_date from wc26_schedule."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT id, home_team, away_team, match_date, stage FROM wc26_schedule WHERE id=?",
        (match_id,),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def store_raw_stats(db_path: Path, raw) -> int:
    """Store raw stats JSON in match_statistics_raw. Returns row id."""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO match_statistics_raw
           (match_id, provider, provider_match_id, source_url, payload_json, payload_hash, status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            raw.match_id,
            raw.provider,
            raw.provider_match_id,
            raw.source_url,
            json.dumps(raw.payload, default=str, ensure_ascii=False),
            raw.payload_hash if hasattr(raw, "payload_hash") else None,
            "fetched",
        ),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def store_team_stats(db_path: Path, stats) -> int:
    """Store normalized TeamMatchStats in match_team_statistics. Returns row id."""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        """INSERT OR REPLACE INTO match_team_statistics
           (match_id, team_name, side, goals, xg, shots_total, shots_on_target,
            shots_inside_box, big_chances, corners,
            possession_pct, passes_attempted, pass_accuracy_pct, final_third_entries,
            tackles, interceptions, clearances, fouls, yellow_cards, red_cards,
            saves, penalties_awarded, penalties_scored, own_goals,
            provider, data_quality_score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            stats.match_id, stats.team_name, stats.side,
            stats.goals, stats.xg, stats.shots_total, stats.shots_on_target,
            stats.shots_inside_box, stats.big_chances, stats.corners,
            stats.possession_pct, stats.passes_attempted, stats.pass_accuracy_pct,
            stats.final_third_entries,
            stats.tackles, stats.interceptions, stats.clearances,
            stats.fouls, stats.yellow_cards, stats.red_cards,
            stats.saves, stats.penalties_awarded, stats.penalties_scored,
            stats.own_goals,
            stats.provider, stats.data_quality_score,
        ),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def insert_manual_stats(
    db_path: Path, match_id: int, home_team: str, away_team: str,
    manual_data: Dict[str, Dict[str, float]]
) -> list:
    """Insert manually provided stats (e.g. xG from web search).

    manual_data format: {"home": {"xg": 1.80, "possession_pct": 58}, "away": {"xg": 0.30}}
    """
    from app.services.match_stats.provider_base import TeamMatchStats
    from app.services.match_stats.quality import compute_data_quality_score

    results = []
    for side, team in [("home", home_team), ("away", away_team)]:
        data = manual_data.get(side, {})
        stats = TeamMatchStats(
            match_id=match_id,
            team_name=team,
            side=side,
            provider="manual_csv",
            xg=data.get("xg"),
            possession_pct=data.get("possession_pct"),
            passes_attempted=data.get("passes_attempted"),
            pass_accuracy_pct=data.get("pass_accuracy_pct"),
            corners=data.get("corners"),
            big_chances=data.get("big_chances"),
            shots_inside_box=data.get("shots_inside_box"),
            clearances=data.get("clearances"),
            final_third_entries=data.get("final_third_entries"),
            shots_total=data.get("shots_total"),
            shots_on_target=data.get("shots_on_target"),
            goals=data.get("goals"),
            fouls=data.get("fouls"),
            yellow_cards=data.get("yellow_cards"),
            red_cards=data.get("red_cards"),
            saves=data.get("saves"),
            tackles=data.get("tackles"),
            interceptions=data.get("interceptions"),
        )
        stats.data_quality_score = compute_data_quality_score({
            "xg": stats.xg, "shots_total": stats.shots_total,
            "shots_on_target": stats.shots_on_target,
            "possession_pct": stats.possession_pct,
            "passes_attempted": stats.passes_attempted,
            "corners": stats.corners, "saves": stats.saves,
            "goals": stats.goals,
        }, side)

        row_id = store_team_stats(db_path, stats)
        results.append(row_id)
        print(f"  {side} ({team}): stored as row {row_id}, quality={stats.data_quality_score:.2f}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Fetch match statistics from providers")
    parser.add_argument("--match-id", type=int, required=True, help="wc26_schedule.id")
    parser.add_argument("--provider", type=str, default="fbref", choices=["fbref", "manual", "all"])
    parser.add_argument("--manual-xg", type=str, help="Comma-separated xG values: home,away (e.g. 1.80,0.30)")
    parser.add_argument("--manual-possession", type=str, help="Comma-separated possession: home,away (e.g. 58,42)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't write to DB")
    parser.add_argument("--db-path", type=str, default=None)
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else DB_PATH
    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}")
        sys.exit(1)

    # Get match info
    match = get_match_info(db_path, args.match_id)
    if match is None:
        print(f"ERROR: Match ID {args.match_id} not found in wc26_schedule")
        sys.exit(1)

    print(f"Match #{match['id']}: {match['home_team']} vs {match['away_team']}")
    print(f"  Date: {match['match_date']}, Stage: {match['stage']}")
    print()

    # --- Provider: FBref ---
    if args.provider in ("fbref", "all"):
        try:
            from app.services.match_stats.fbref_provider import FBrefProvider
            from app.services.match_stats.quality import compute_data_quality_score

            print("Fetching from FBref...")
            provider = FBrefProvider()
            raw = provider.fetch_match_stats(
                args.match_id, match["home_team"], match["away_team"]
            )

            if args.dry_run:
                print(f"  [DRY RUN] Would store raw stats: {len(str(raw.payload))} bytes")
            else:
                raw_id = store_raw_stats(db_path, raw)
                print(f"  Raw stats stored: row {raw_id}")

            # Normalize and store per-team stats
            for side, team in [("home", match["home_team"]), ("away", match["away_team"])]:
                try:
                    team_stats = provider.normalize(raw, side)
                    team_stats.data_quality_score = compute_data_quality_score({
                        "goals": team_stats.goals,
                        "shots_total": team_stats.shots_total,
                        "shots_on_target": team_stats.shots_on_target,
                        "fouls": team_stats.fouls,
                        "saves": team_stats.saves,
                        "tackles": team_stats.tackles,
                        "interceptions": team_stats.interceptions,
                    }, side)
                except Exception as e:
                    print(f"  WARNING: Could not normalize {side} stats: {e}")
                    continue

                if args.dry_run:
                    print(f"  [DRY RUN] {side} ({team}): {team_stats}")
                else:
                    row_id = store_team_stats(db_path, team_stats)
                    print(f"  {side} ({team}): stored as row {row_id}, "
                          f"shots={team_stats.shots_total}, SoT={team_stats.shots_on_target}, "
                          f"quality={team_stats.data_quality_score:.2f}")

        except Exception as e:
            print(f"  ERROR (FBref): {e}")
            import traceback
            traceback.print_exc()

    # --- Manual xG / possession ---
    manual_data = {}
    if args.manual_xg:
        parts = [float(x.strip()) for x in args.manual_xg.split(",")]
        manual_data.setdefault("home", {})["xg"] = parts[0]
        manual_data.setdefault("away", {})["xg"] = parts[1]
        print(f"\nManual xG: home={parts[0]}, away={parts[1]}")

    if args.manual_possession:
        parts = [float(x.strip()) for x in args.manual_possession.split(",")]
        manual_data.setdefault("home", {})["possession_pct"] = parts[0]
        manual_data.setdefault("away", {})["possession_pct"] = parts[1]
        print(f"Manual possession: home={parts[0]}%, away={parts[1]}%")

    if manual_data and not args.dry_run:
        print("\nStoring manual stats...")
        insert_manual_stats(db_path, args.match_id, match["home_team"], match["away_team"], manual_data)

    print("\nDone.")


if __name__ == "__main__":
    main()
