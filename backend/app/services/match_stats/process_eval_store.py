"""process_eval_store.py — Shared DB helpers for process evaluation scripts.

Both ``run_process_eval.py`` and ``batch_process_eval.py`` need to read
match metadata, snapshots, and write process-eval results.  This module
provides the canonical implementations.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional


def get_match_from_schedule(db: sqlite3.Connection, match_id: int) -> Optional[Dict[str, Any]]:
    """Return wc26_schedule row dict for *match_id*, or None."""
    db.row_factory = sqlite3.Row
    cur = db.execute("SELECT * FROM wc26_schedule WHERE id=?", (match_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def get_snapshot_for_match(
    db: sqlite3.Connection, home_team: str, away_team: str,
) -> Optional[Dict[str, Any]]:
    """Return the most recent pre_match_snapshots row for a team pair."""
    db.row_factory = sqlite3.Row
    cur = db.execute(
        "SELECT * FROM pre_match_snapshots "
        "WHERE home_team=? AND away_team=? "
        "ORDER BY snapshot_at DESC LIMIT 1",
        (home_team, away_team),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def store_process_eval(
    db: sqlite3.Connection,
    eval_result: Any,          # ProcessEvalResult
    classification: Dict[str, Any],
    learning_weight: float,
    match_context: Dict[str, Any],
    component_signals: Dict[str, Any],
    learning_tier: str,
) -> int:
    """Insert (or replace) a process evaluation row in postmatch_process_eval.

    Returns the rowid of the inserted/updated row.
    """
    data = {
        "match_id": eval_result.match_id,
        "predicted_home_xg": eval_result.predicted_home_xg,
        "predicted_away_xg": eval_result.predicted_away_xg,
        "actual_home_xg": eval_result.actual_home_xg,
        "actual_away_xg": eval_result.actual_away_xg,
        "actual_home_goals": eval_result.actual_home_goals,
        "actual_away_goals": eval_result.actual_away_goals,
        "xg_home_error": eval_result.xg_home_error,
        "xg_away_error": eval_result.xg_away_error,
        "xg_mae": eval_result.xg_mae,
        "xg_direction_correct": eval_result.xg_direction_correct,
        "predicted_total_goals": eval_result.predicted_total_goals,
        "actual_total_xg": eval_result.actual_total_xg,
        "total_xg_error": eval_result.total_xg_error,
        "finishing_delta_home": eval_result.finishing_delta_home,
        "finishing_delta_away": eval_result.finishing_delta_away,
        "dominance_index_home": eval_result.dominance_index_home,
        "dominance_index_away": eval_result.dominance_index_away,
        "process_winner": eval_result.process_winner,
        "outcome_correct": int(eval_result.outcome_correct),
        "process_correct": int(eval_result.process_correct),
        "xg_result_alignment": eval_result.xg_result_alignment,
        "process_label": eval_result.process_label,
        "model_failure_type": classification["model_failure_type"],
        "learning_weight": learning_weight,
        "recommended_action": f"tier={learning_tier}",
        "notes": json.dumps({
            "classification_reason": classification["reason"],
            "base_learning_weight": classification["base_learning_weight"],
            "match_context": match_context,
            "component_signals": component_signals,
        }, ensure_ascii=False),
    }
    cols = list(data.keys())
    vals = list(data.values())
    placeholders = ["?"] * len(cols)
    sql = (
        f"INSERT OR REPLACE INTO postmatch_process_eval "
        f"({', '.join(cols)}) VALUES ({', '.join(placeholders)})"
    )
    cur = db.cursor()
    cur.execute(sql, vals)
    return cur.lastrowid


def determine_actual_result(home_goals: Any, away_goals: Any) -> Optional[str]:
    """Return 'home', 'away', 'draw', or None if goals are missing."""
    if home_goals is None or away_goals is None:
        return None
    hg = int(home_goals)
    ag = int(away_goals)
    if hg > ag:
        return "home"
    elif ag > hg:
        return "away"
    else:
        return "draw"


def determine_predicted_winner(probs: Dict[str, float]) -> Optional[str]:
    """Return 'home', 'draw', 'away' from probability dict."""
    if not probs:
        return None
    return max(probs, key=probs.get)


def build_classification_pipeline(
    *,
    outcome_correct: bool,
    xg_direction_correct: Optional[int],
    xg_mae: Optional[float],
    data_quality_score: float,
    match_context: Dict[str, Any],
    component_signals: Dict[str, Any],
    snapshot_complete: bool,
):
    """Run classification + learning weight + tier calculation.

    Returns (classification_dict, learning_weight, tier_name).
    """
    from backend.app.services.match_stats.failure_classifier import (
        classify_failure,
        compute_learning_weight,
        get_learning_tier,
    )

    classification = classify_failure(
        outcome_correct=outcome_correct,
        xg_direction_correct=xg_direction_correct,
        xg_mae=xg_mae,
        data_quality_score=data_quality_score,
        match_context=match_context,
        component_signals=component_signals,
    )

    lw = compute_learning_weight(
        model_failure_type=classification["model_failure_type"],
        data_quality_score=data_quality_score,
        snapshot_complete=snapshot_complete,
        match_context=match_context,
    )
    tier = get_learning_tier(lw)
    return classification, lw, tier
