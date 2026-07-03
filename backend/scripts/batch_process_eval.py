#!/usr/bin/env python
"""
Batch process all 7 KO matches: insert stats → evaluate → classify → store.
Matches without snapshots use memory-derived predicted xG (marked clearly).
"""
import sqlite3, sys, json
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "local_stage2.db"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.services.match_stats.process_evaluator import evaluate_process
from backend.app.services.match_stats.failure_classifier import classify_failure, compute_learning_weight, get_learning_tier


def get_match(db, mid):
    db.row_factory = sqlite3.Row
    cur = db.execute("SELECT * FROM wc26_schedule WHERE id=?", (mid,))
    row = cur.fetchone()
    return dict(row) if row else None


def get_snapshot(db, home_team, away_team):
    db.row_factory = sqlite3.Row
    cur = db.execute(
        "SELECT * FROM pre_match_snapshots WHERE home_team=? AND away_team=? ORDER BY snapshot_at DESC LIMIT 1",
        (home_team, away_team),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def upsert_team_stats(db, match_id, side, team_name, data):
    """Insert or update team stats for one side."""
    fields = [
        "goals", "xg", "shots_total", "shots_on_target", "shots_inside_box",
        "big_chances", "corners", "possession_pct", "passes_attempted",
        "pass_accuracy_pct", "final_third_entries", "tackles", "interceptions",
        "clearances", "fouls", "yellow_cards", "red_cards", "saves",
    ]
    col_names = []
    values = []
    for f in fields:
        if f in data and data[f] is not None:
            col_names.append(f)
            values.append(data[f])

    from backend.app.services.match_stats.quality import compute_data_quality_score
    quality = compute_data_quality_score(data, side)
    col_names.append("data_quality_score")
    values.append(quality)

    # Delete old row if exists, then insert fresh
    db.execute("DELETE FROM match_team_statistics WHERE match_id=? AND side=?", (match_id, side))

    placeholders = ["?"] * len(col_names)
    all_cols = col_names + ["match_id", "team_name", "side", "provider"]
    all_vals = values + [match_id, team_name, side, "manual_csv"]
    sql = f"INSERT INTO match_team_statistics ({', '.join(all_cols)}) VALUES ({', '.join(['?']*len(all_vals))})"
    db.execute(sql, all_vals)
    return quality


def store_process_eval(db, eval_result, classification, lw, match_context, component_signals):
    """Insert process evaluation result using dynamic column-value matching."""
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
        "learning_weight": lw,
        "recommended_action": f"tier={get_learning_tier(lw)}",
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
    sql = f"INSERT OR REPLACE INTO postmatch_process_eval ({', '.join(cols)}) VALUES ({', '.join(placeholders)})"
    cur = db.cursor()
    cur.execute(sql, vals)
    return cur.lastrowid


# ── Match data (from WebSearch) ──────────────────────────────────────────

MATCHES = {
    177: {  # South Africa 0-1 Canada (home=SAF, away=CAN)
        "note": "NO snapshot — DC xG from report: SAF 0.60, CAN 1.10",
        "predicted_home_xg": 0.60,  # SAF = home
        "predicted_away_xg": 1.10,  # CAN = away
        "predicted_probs": {"home": 0.298, "draw": 0.214, "away": 0.488},  # post-cal from report
        "home": {"goals": 0, "xg": 0.20, "shots_total": 6, "shots_on_target": 1, "possession_pct": 58,
                 "corners": 1, "passes_attempted": 248, "fouls": 10, "yellow_cards": 0, "red_cards": 0, "saves": 6},
        "away": {"goals": 1, "xg": 1.55, "shots_total": 12, "shots_on_target": 7, "possession_pct": 42,
                 "corners": 4, "passes_attempted": 192, "fouls": 16, "yellow_cards": 2, "red_cards": 0, "saves": 1},
        "context": {"venue_home_advantage_missed": False},
        "signals": {},
    },
    178: {  # Germany 1-1 Paraguay (PAR won on pens)
        "note": "Snapshot exists",
        "predicted_probs": {"home": 0.70, "draw": 0.20, "away": 0.10},  # approx from snapshot xG
        "home": {"goals": 1, "xg": 1.49, "shots_total": 21, "shots_on_target": 7, "shots_inside_box": 12,
                 "big_chances": 3, "corners": 11, "possession_pct": 65,
                 "passes_attempted": 600, "pass_accuracy_pct": 88,
                 "fouls": 8, "yellow_cards": 2, "red_cards": 0, "saves": 2},
        "away": {"goals": 1, "xg": 0.42, "shots_total": 7, "shots_on_target": 3, "shots_inside_box": 3,
                 "big_chances": 1, "corners": 4, "possession_pct": 35,
                 "passes_attempted": 250, "pass_accuracy_pct": 72,
                 "fouls": 14, "yellow_cards": 3, "red_cards": 0, "saves": 6},
        "context": {"venue_home_advantage_missed": False},
        "signals": {"weibull_extreme_wrong": True},  # GER 70% Weibull was way off
    },
    179: {  # Netherlands 1-1 Morocco (home=NED, away=MAR)
        "note": "NO snapshot — DC xG from report: NED 0.89, MAR 1.34",
        "predicted_home_xg": 0.89,  # NED = home
        "predicted_away_xg": 1.34,  # MAR = away (DC favored Morocco!)
        "predicted_probs": {"home": 0.487, "draw": 0.207, "away": 0.306},  # post-cal from report
        "home": {"goals": 1, "xg": 0.25, "shots_total": 6, "shots_on_target": 2, "shots_inside_box": 3,
                 "big_chances": 1, "corners": 5, "possession_pct": 30,
                 "passes_attempted": 373, "pass_accuracy_pct": 79,
                 "fouls": 12, "yellow_cards": 3, "red_cards": 0, "saves": 4},
        "away": {"goals": 1, "xg": 1.33, "shots_total": 11, "shots_on_target": 5, "shots_inside_box": 7,
                 "big_chances": 5, "corners": 8, "possession_pct": 70,
                 "passes_attempted": 878, "pass_accuracy_pct": 91,
                 "fouls": 10, "yellow_cards": 2, "red_cards": 0, "saves": 1},
        "context": {"venue_home_advantage_missed": False},
        "signals": {"pi_single_upset_overreaction": True},  # Pi was heavily on Morocco
    },
    180: {  # Brazil 2-1 Japan
        "note": "Snapshot exists",
        "predicted_probs": {"home": 0.45, "draw": 0.28, "away": 0.27},
        "home": {"goals": 2, "xg": 1.70, "shots_total": 19, "shots_on_target": 7, "shots_inside_box": 12,
                 "big_chances": 4, "corners": 6, "possession_pct": 69,
                 "passes_attempted": 715, "pass_accuracy_pct": 91,
                 "fouls": 4, "yellow_cards": 2, "red_cards": 0, "saves": 0},
        "away": {"goals": 1, "xg": 0.23, "shots_total": 5, "shots_on_target": 2, "shots_inside_box": 2,
                 "big_chances": 0, "corners": 2, "possession_pct": 31,
                 "passes_attempted": 330, "pass_accuracy_pct": 85,
                 "fouls": 13, "yellow_cards": 3, "red_cards": 0, "saves": 5},
        "context": {"venue_home_advantage_missed": False},
        "signals": {},
    },
    182: {  # Cote d'Ivoire 1-2 Norway
        "note": "Snapshot exists",
        "predicted_probs": {"home": 0.22, "draw": 0.24, "away": 0.54},
        "home": {"goals": 1, "xg": 1.32, "shots_total": 14, "shots_on_target": 5, "shots_inside_box": 8,
                 "big_chances": 2, "corners": 14, "possession_pct": 47,
                 "passes_attempted": 188, "pass_accuracy_pct": 78,
                 "fouls": 14, "yellow_cards": 2, "red_cards": 0, "saves": 1},
        "away": {"goals": 2, "xg": 1.96, "shots_total": 9, "shots_on_target": 4, "shots_inside_box": 5,
                 "big_chances": 4, "corners": 3, "possession_pct": 53,
                 "passes_attempted": 256, "pass_accuracy_pct": 81,
                 "fouls": 12, "yellow_cards": 1, "red_cards": 0, "saves": 4},
        "context": {"venue_home_advantage_missed": False},
        "signals": {},
    },
    184: {  # France 3-0 Sweden
        "note": "Snapshot exists",
        "predicted_probs": {"home": 0.55, "draw": 0.25, "away": 0.20},
        "home": {"goals": 3, "xg": 3.07, "shots_total": 25, "shots_on_target": 12, "shots_inside_box": 16,
                 "big_chances": 6, "corners": 9, "possession_pct": 65,
                 "passes_attempted": 529, "pass_accuracy_pct": 88,
                 "fouls": 11, "yellow_cards": 1, "red_cards": 0, "saves": 1},
        "away": {"goals": 0, "xg": 0.50, "shots_total": 5, "shots_on_target": 2, "shots_inside_box": 2,
                 "big_chances": 0, "corners": 0, "possession_pct": 35,
                 "passes_attempted": 285, "pass_accuracy_pct": 78,
                 "fouls": 10, "yellow_cards": 2, "red_cards": 0, "saves": 9},
        "context": {"venue_home_advantage_missed": False},
        "signals": {},
    },
}


def main():
    db = sqlite3.connect(str(DB_PATH))

    for mid in sorted(MATCHES.keys()):
        cfg = MATCHES[mid]
        match = get_match(db, mid)
        if not match:
            print(f"#{mid}: NOT FOUND, skipping")
            continue

        home_team = match["home_team"]
        away_team = match["away_team"]
        score = f"{match['home_goals']}-{match['away_goals']}"

        print(f"\n{'='*60}")
        print(f"#{mid} {home_team} vs {away_team}  {score}  [{cfg.get('note', '')}]")
        print(f"{'='*60}")

        # ── Get snapshot ──
        snap = get_snapshot(db, home_team, away_team)
        if snap:
            pred_home_xg = snap.get("home_xg")
            pred_away_xg = snap.get("away_xg")
            probs = {
                "home": snap.get("final_home_prob", 0),
                "draw": snap.get("final_draw_prob", 0),
                "away": snap.get("final_away_prob", 0),
            }
            print(f"  Snapshot: pred_xG H={pred_home_xg:.3f} A={pred_away_xg:.3f}, "
                  f"probs H={probs['home']:.3f} D={probs['draw']:.3f} A={probs['away']:.3f}")
        else:
            pred_home_xg = cfg.get("predicted_home_xg")
            pred_away_xg = cfg.get("predicted_away_xg")
            probs = cfg.get("predicted_probs", {})
            print(f"  NO SNAPSHOT — using memory pred_xG H={pred_home_xg} A={pred_away_xg}")

        # ── Upsert team stats ──
        q_home = upsert_team_stats(db, mid, "home", home_team, cfg["home"])
        q_away = upsert_team_stats(db, mid, "away", away_team, cfg["away"])
        data_quality = max(q_home, q_away)
        print(f"  Stats inserted: quality H={q_home:.2f} A={q_away:.2f}")

        # ── Determine results ──
        predicted_winner = max(probs, key=probs.get) if probs else None
        if match["home_goals"] > match["away_goals"]:
            actual_result = "home"
        elif match["away_goals"] > match["home_goals"]:
            actual_result = "away"
        else:
            actual_result = "draw"
        outcome_correct = (predicted_winner == actual_result)

        # ── Evaluate ──
        result = evaluate_process(
            match_id=mid,
            predicted_home_xg=pred_home_xg,
            predicted_away_xg=pred_away_xg,
            home_stats=cfg["home"],
            away_stats=cfg["away"],
            outcome_correct=outcome_correct,
            predicted_winner=predicted_winner,
        )

        # ── Classify ──
        classification = classify_failure(
            outcome_correct=outcome_correct,
            xg_direction_correct=result.xg_direction_correct,
            xg_mae=result.xg_mae,
            data_quality_score=data_quality,
            match_context=cfg.get("context", {}),
            component_signals=cfg.get("signals", {}),
        )

        lw = compute_learning_weight(
            model_failure_type=classification["model_failure_type"],
            data_quality_score=data_quality,
            snapshot_complete=(snap is not None),
            match_context=cfg.get("context", {}),
        )
        tier = get_learning_tier(lw)

        # ── Store ──
        row_id = store_process_eval(db, result, classification, lw, cfg.get("context", {}), cfg.get("signals", {}))

        # ── Print ──
        print(f"  xG Error:      H={result.xg_home_error:+.3f} A={result.xg_away_error:+.3f} MAE={result.xg_mae:.3f}")
        print(f"  Direction:     pred_winner={predicted_winner} actual={actual_result} "
              f"outcome_correct={outcome_correct} xG_dir={'OK' if result.xg_direction_correct else 'WRONG'}")
        print(f"  Dominance:     H={result.dominance_index_home:.3f} A={result.dominance_index_away:.3f} "
              f"finish H={result.finishing_delta_home:+.2f} A={result.finishing_delta_away:+.2f}")
        print(f"  Process Label: {result.process_label}")
        print(f"  Failure Type:  {classification['model_failure_type']}")
        print(f"  Learning Wt:   {lw:.4f} → tier={tier}")
        print(f"  Stored:        row {row_id}")

    db.commit()
    db.close()
    print(f"\n{'='*60}")
    print("All 7 KO matches processed.")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
