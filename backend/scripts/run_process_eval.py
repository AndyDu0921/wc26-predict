#!/usr/bin/env python
"""
V4.6-process-eval: End-to-end process evaluation for a completed match.

Data flow:
  pre_match_snapshots ─┐
                        ├─→ process_evaluator ─→ failure_classifier ─→ postmatch_process_eval
  match_team_statistics ─┘

Usage:
  python backend/scripts/run_process_eval.py --match-id 183
  python backend/scripts/run_process_eval.py --match-id 183 --data '{...}'
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "local_stage2.db"

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

def get_snapshot(db_path: Path, match_id: str) -> Optional[Dict]:
    """Get the most recent prediction snapshot for a match (by team name match)."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # First get the match info
    cur = conn.execute(
        "SELECT id, home_team, away_team, home_goals, away_goals, stage, venue, city "
        "FROM wc26_schedule WHERE id=?",
        (match_id,),
    )
    match = cur.fetchone()
    if not match:
        conn.close()
        return None

    # Find snapshot by team names
    cur = conn.execute(
        """SELECT * FROM pre_match_snapshots
           WHERE home_team=? AND away_team=?
           ORDER BY snapshot_at DESC LIMIT 1""",
        (match["home_team"], match["away_team"]),
    )
    snap = cur.fetchone()
    conn.close()

    if not snap:
        return None

    return {
        "match": dict(match),
        "snapshot": dict(snap),
    }


def get_team_stats(db_path: Path, match_id: int) -> Dict:
    """Get actual team stats from match_team_statistics."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    cur = conn.execute(
        "SELECT * FROM match_team_statistics WHERE match_id=? ORDER BY side",
        (match_id,),
    )
    rows = cur.fetchall()
    conn.close()

    home_stats = {}
    away_stats = {}
    for row in rows:
        d = dict(row)
        if d["side"] == "home":
            home_stats = d
        else:
            away_stats = d

    return {"home": home_stats, "away": away_stats}


def update_team_stats(db_path: Path, match_id: int, data: Dict) -> None:
    """Update match_team_statistics with full match data, re-computing quality."""
    from app.services.match_stats.quality import compute_data_quality_score

    conn = sqlite3.connect(str(db_path))

    fields = [
        "goals", "xg", "shots_total", "shots_on_target", "shots_inside_box",
        "big_chances", "corners", "possession_pct", "passes_attempted",
        "pass_accuracy_pct", "final_third_entries", "tackles", "interceptions",
        "clearances", "fouls", "yellow_cards", "red_cards", "saves",
    ]

    for side in ("home", "away"):
        side_data = data.get(side, {})
        if not side_data:
            continue

        set_clauses = []
        values = []
        for f in fields:
            if f in side_data and side_data[f] is not None:
                set_clauses.append(f"{f}=?")
                values.append(side_data[f])

        if set_clauses:
            # Re-compute quality score with full data
            new_quality = compute_data_quality_score(side_data, side)
            set_clauses.append("data_quality_score=?")
            values.append(new_quality)

            values.extend([match_id, side])
            sql = f"UPDATE match_team_statistics SET {', '.join(set_clauses)} WHERE match_id=? AND side=?"
            conn.execute(sql, values)

    conn.commit()
    conn.close()
    print(f"  Updated team stats with {len(data.get('home', {}))} fields each + recomputed quality")


def store_process_eval(db_path: Path, eval_result) -> int:
    """Store process evaluation result in postmatch_process_eval."""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute(
        """INSERT OR REPLACE INTO postmatch_process_eval
           (match_id, predicted_home_xg, predicted_away_xg,
            actual_home_xg, actual_away_xg, actual_home_goals, actual_away_goals,
            xg_home_error, xg_away_error, xg_mae, xg_direction_correct,
            predicted_total_goals, actual_total_xg, total_xg_error,
            finishing_delta_home, finishing_delta_away,
            dominance_index_home, dominance_index_away,
            process_winner, outcome_correct, process_correct,
            xg_result_alignment, process_label,
            model_failure_type, learning_weight, recommended_action, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            eval_result.match_id,
            eval_result.predicted_home_xg,
            eval_result.predicted_away_xg,
            eval_result.actual_home_xg,
            eval_result.actual_away_xg,
            eval_result.actual_home_goals,
            eval_result.actual_away_goals,
            eval_result.xg_home_error,
            eval_result.xg_away_error,
            eval_result.xg_mae,
            eval_result.xg_direction_correct,
            eval_result.predicted_total_goals,
            eval_result.actual_total_xg,
            eval_result.total_xg_error,
            eval_result.finishing_delta_home,
            eval_result.finishing_delta_away,
            eval_result.dominance_index_home,
            eval_result.dominance_index_away,
            eval_result.process_winner,
            int(eval_result.outcome_correct),
            int(eval_result.process_correct),
            eval_result.xg_result_alignment,
            eval_result.process_label,
            # These will be filled after classification
            eval_result.model_failure_type if hasattr(eval_result, "model_failure_type") else None,
            eval_result.learning_weight if hasattr(eval_result, "learning_weight") else None,
            eval_result.recommended_action if hasattr(eval_result, "recommended_action") else None,
            eval_result.notes if hasattr(eval_result, "notes") else None,
        ),
    )

    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def main():
    parser = argparse.ArgumentParser(description="Run end-to-end process evaluation")
    parser.add_argument("--match-id", type=int, required=True)
    parser.add_argument("--data", type=str, default=None,
                        help="JSON with match stats: '{\"home\":{...}, \"away\":{...}}'")
    parser.add_argument("--db-path", type=str, default=None)
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else DB_PATH

    # Step 1: Get snapshot
    snapshot = get_snapshot(db_path, str(args.match_id))
    if not snapshot:
        print(f"ERROR: No snapshot found for match #{args.match_id}")
        sys.exit(1)

    match = snapshot["match"]
    snap = snapshot["snapshot"]

    print(f"\n{'='*60}")
    print(f"Process Evaluation — Match #{args.match_id}")
    print(f"{'='*60}")
    print(f"Teams: {match['home_team']} vs {match['away_team']}")
    print(f"Score: {match['home_goals']} - {match['away_goals']}")
    print(f"Venue: {match['venue']}, {match['city']} ({match['stage']})")
    print()

    # Step 2: Update team stats if data provided
    if args.data:
        data = json.loads(args.data)
        update_team_stats(db_path, args.match_id, data)

    # Step 3: Get actual stats from DB
    team_stats = get_team_stats(db_path, args.match_id)
    home_stats = team_stats["home"]
    away_stats = team_stats["away"]

    print("Team Stats (from DB):")
    for s, name in [("home", match['home_team']), ("away", match['away_team'])]:
        d = team_stats[s]
        provider = d.get("provider", "unknown")
        quality = d.get("data_quality_score", "?")
        print(f"  {name}: xG={d.get('xg')}, shots={d.get('shots_total')}, "
              f"SoT={d.get('shots_on_target')}, poss={d.get('possession_pct')}%, "
              f"corners={d.get('corners')}, provider={provider}, quality={quality}")
    print()

    # Step 4: Run process evaluator
    from app.services.match_stats.process_evaluator import (
        evaluate_process,
    )

    # Determine predicted winner from snapshot
    probs = {
        "home": snap.get("final_home_prob", 0),
        "draw": snap.get("final_draw_prob", 0),
        "away": snap.get("final_away_prob", 0),
    }
    predicted_winner = max(probs, key=probs.get) if probs else None

    # Was outcome correct?
    actual_result = None
    if match["home_goals"] is not None and match["away_goals"] is not None:
        if match["home_goals"] > match["away_goals"]:
            actual_result = "home"
        elif match["away_goals"] > match["home_goals"]:
            actual_result = "away"
        else:
            actual_result = "draw"
    outcome_correct = (predicted_winner == actual_result) if actual_result else False

    result = evaluate_process(
        match_id=args.match_id,
        predicted_home_xg=snap.get("home_xg"),
        predicted_away_xg=snap.get("away_xg"),
        home_stats=home_stats,
        away_stats=away_stats,
        outcome_correct=outcome_correct,
        predicted_winner=predicted_winner,
    )

    # Step 5: Run failure classifier
    from app.services.match_stats.failure_classifier import (
        classify_failure,
        compute_learning_weight,
        get_learning_tier,
    )

    data_quality = max(
        home_stats.get("data_quality_score", 0.5) or 0.5,
        away_stats.get("data_quality_score", 0.5) or 0.5,
    )

    match_context = {
        "venue_home_advantage_missed": True,  # Azteca!
        "elo_default_value": False,
    }
    component_signals = {
        "market_high_consensus_correct": False,
        "weibull_extreme_wrong": False,
        "pi_single_upset_overreaction": False,
    }

    classification = classify_failure(
        outcome_correct=outcome_correct,
        xg_direction_correct=result.xg_direction_correct,
        xg_mae=result.xg_mae,
        data_quality_score=data_quality,
        match_context=match_context,
        component_signals=component_signals,
    )

    # Step 6: Compute learning weight
    lw = compute_learning_weight(
        model_failure_type=classification["model_failure_type"],
        data_quality_score=data_quality,
        snapshot_complete=True,
        match_context=match_context,
        process_verified=classification.get("process_verified", True),
    )
    tier = get_learning_tier(lw)

    # Attach to result
    result.model_failure_type = classification["model_failure_type"]
    result.learning_weight = lw
    result.recommended_action = f"tier={tier}"
    result.notes = json.dumps({
        "classification_reason": classification["reason"],
        "base_learning_weight": classification["base_learning_weight"],
        "match_context": match_context,
        "component_signals": component_signals,
    }, ensure_ascii=False)

    # Step 7: Store in DB
    row_id = store_process_eval(db_path, result)

    # Step 8: Print summary
    print(f"{'='*60}")
    print("EVALUATION RESULTS")
    print(f"{'='*60}")
    print("")
    print("--- Prediction vs Actual ---")
    print(f"  Predicted xG:   H={result.predicted_home_xg:.4f}  A={result.predicted_away_xg:.4f}  total={result.predicted_total_goals:.4f}")
    print(f"  Actual xG:      H={result.actual_home_xg:.4f}  A={result.actual_away_xg:.4f}  total={result.actual_total_xg:.4f}")
    print(f"  xG Error:       H={result.xg_home_error:+.4f}  A={result.xg_away_error:+.4f}  MAE={result.xg_mae:.4f}")
    print(f"  Total Goal Err: {result.total_xg_error:+.4f}")
    print("")
    print("--- Direction ---")
    print(f"  Predicted winner: {predicted_winner} (H={probs['home']:.3f} D={probs['draw']:.3f} A={probs['away']:.3f})")
    print(f"  Actual winner:    {actual_result} ({match['home_goals']}-{match['away_goals']})")
    print(f"  Outcome correct:  {outcome_correct}")
    print(f"  xG direction:     {'correct' if result.xg_direction_correct == 1 else 'wrong' if result.xg_direction_correct == 0 else 'N/A'}")
    print(f"  Process winner:   {result.process_winner}")
    print("")
    print("--- Dominance ---")
    print(f"  Home: {result.dominance_index_home:.4f}  Away: {result.dominance_index_away:.4f}")
    print(f"  Finishing: H={result.finishing_delta_home:+.4f}  A={result.finishing_delta_away:+.4f}")
    print("")
    print("--- Classification ---")
    print(f"  Process Label:     {result.process_label}")
    print(f"  Failure Type:      {result.model_failure_type}")
    print(f"  Learning Weight:   {result.learning_weight:.4f}")
    print(f"  Learning Tier:     {tier}")
    print(f"  Recommended:       {result.recommended_action}")
    print("")
    print(f"  Stored in postmatch_process_eval: row {row_id}")
    print(f"{'='*60}")
    print("Done.")


if __name__ == "__main__":
    main()
