#!/usr/bin/env python3
"""Backfill postmatch_eval records from memory files and V3.8+ snapshots.

Usage:
    python scripts/backfill_postmatch_evals.py --dry-run      # preview only
    python scripts/backfill_postmatch_evals.py --from-memory   # 5 manual reviews → DB
    python scripts/backfill_postmatch_evals.py --auto          # auto-backfill V3.8+ snapshots
    python scripts/backfill_postmatch_evals.py --all           # both modes
    python scripts/backfill_postmatch_evals.py --allow-placeholder-runs  # legacy escape hatch
"""
import sys, json, uuid, os
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


# ── Memory file → match mapping ──
# Extracted from MEMORY.md index + memory file content
MEMORY_EVALS = [
    {
        "memory_file": "brazil-haiti-postmatch-20260620.md",
        "home_team": "Brazil",
        "away_team": "Haiti",
        "match_date": "2026-06-20",
        "actual_home_goals": 3,
        "actual_away_goals": 0,
        "brier_score": 0.2159,
        "components": {
            "dc": {"direction": True, "brier": 0.2057},
            "enhancer": {"direction": False, "brier": 0.2957},
            "elo": {"direction": True, "brier": 0.1898},
            "pi": {"direction": True, "brier": None},
            "market": {"direction": True, "brier": None},
        },
        "notes": "Brazil 'early-kill' effect. DC best component. Enhancer continues overrating underdogs. WC xG calibration factor may be too high.",
    },
    {
        "memory_file": "spain-saudi-postmatch-20260622.md",
        "home_team": "Spain",
        "away_team": "Saudi Arabia",
        "match_date": "2026-06-21",
        "actual_home_goals": 4,
        "actual_away_goals": 0,
        "brier_score": 0.179,  # with market 40%
        "components": {
            "dc": {"direction": True, "brier": 0.0964},
            "enhancer": {"direction": False, "brier": 0.6952},
            "elo": {"direction": True, "brier": 0.1070},
            "pi": {"direction": True, "brier": 0.0788},
            "market": {"direction": True, "brier": 0.0234},
        },
        "notes": "Enhancer reverse-predicted (favored Saudi 37.5%). Market data was critical. Divergence-adaptive DC penalty was harmful when DC is right and Enhancer is wrong.",
    },
    {
        "memory_file": "argentina-austria-postmatch-20260623.md",
        "home_team": "Argentina",
        "away_team": "Austria",
        "match_date": "2026-06-22",
        "actual_home_goals": 2,
        "actual_away_goals": 0,
        "brier_score": 0.18,  # market-based Brier
        "components": {
            "dc": {"direction": True, "brier": None},
            "enhancer": {"direction": False, "brier": None},
            "elo": {"direction": True, "brier": None},
            "pi": {"direction": True, "brier": None},
            "market": {"direction": True, "brier": 0.18},
        },
        "notes": "Market saved prediction (Brier 0.18). Enhancer 3/3 wrong in WC. DC xG 2.5x underestimate. Elo underrated.",
    },
    {
        "memory_file": "france-iraq-postmatch-20260623.md",
        "home_team": "France",
        "away_team": "Iraq",
        "match_date": "2026-06-22",
        "actual_home_goals": 3,
        "actual_away_goals": 0,
        "brier_score": 0.016,  # market Brier — near perfect
        "components": {
            "dc": {"direction": True, "brier": None},
            "enhancer": {"direction": False, "brier": None},
            "elo": {"direction": True, "brier": None},
            "pi": {"direction": True, "brier": None},
            "market": {"direction": True, "brier": 0.016},
        },
        "notes": "Market nearly perfect (Brier 0.016). Enhancer 4/4 wrong in WC. DC xG 2x underestimate again. Weather prediction validated (thunderstorms delayed match 2h).",
    },
    {
        "memory_file": "norway-senegal-postmatch-20260623.md",
        "home_team": "Norway",
        "away_team": "Senegal",
        "match_date": "2026-06-23",
        "actual_home_goals": 3,
        "actual_away_goals": 2,
        "brier_score": 0.291,  # Pi Brier — best model
        "components": {
            "dc": {"direction": True, "brier": None},
            "enhancer": {"direction": False, "brier": 1.37},
            "elo": {"direction": False, "brier": None},
            "pi": {"direction": True, "brier": 0.291},
            "market": {"direction": True, "brier": 0.47},
        },
        "notes": "FIRST competitive WC match. Pi was BEST model (Brier 0.29). 7/11 layers wrong direction. Calibration saved prediction. Elo first direction wrong. Enhancer 5/5 wrong.",
    },
]

# Matches that already have DB eval (skip)
SKIP_MATCHES = [
    ("Portugal", "Uzbekistan"),
    ("England", "Ghana"),
]


def find_snapshot(home_team: str, away_team: str, match_date: str) -> dict | None:
    """Find a prediction_snapshot for a given match."""
    import sqlite3
    db_path = str(BACKEND_DIR / "data" / "local_stage2.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Try multiple date formats
    patterns = [match_date, f"{match_date}T00:00:00", f"{match_date}T00:00:00Z"]

    for pattern in patterns:
        cur.execute("""
            SELECT id, match_id, model_version, home_team, away_team, match_time,
                   adjusted_probs, baseline_probs, market_probs, component_probs
            FROM prediction_snapshots
            WHERE home_team = ? AND away_team = ?
              AND match_time LIKE ?
            ORDER BY generated_at DESC
            LIMIT 1
        """, (home_team, away_team, f"{match_date}%"))
        row = cur.fetchone()
        if row:
            cols = [c[0] for c in cur.description]
            result = dict(zip(cols, row))
            conn.close()
            return result

    conn.close()
    return None


def find_prediction_run(snapshot: dict) -> str | None:
    """Find or create a prediction_run linked to this snapshot's match_id."""
    import sqlite3
    db_path = str(BACKEND_DIR / "data" / "local_stage2.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    match_id = snapshot["match_id"]
    if not match_id:
        conn.close()
        return None

    # Try to find an existing prediction_run for this match
    cur.execute("""
        SELECT id FROM prediction_runs
        WHERE match_id = ?
        ORDER BY created_at DESC
        LIMIT 1
    """, (match_id,))
    row = cur.fetchone()
    if row:
        conn.close()
        return row[0]

    conn.close()
    return None


def create_postmatch_eval(
    prediction_run_id: str,
    home_goals: int,
    away_goals: int,
    brier: float,
    notes: str,
    *,
    dry_run: bool = False,
) -> str:
    """Create a postmatch_eval record. Returns the eval ID."""
    import sqlite3
    eval_id = uuid.uuid4().hex[:32]

    actual_result = "H" if home_goals > away_goals else ("A" if away_goals > home_goals else "D")
    log_loss = -1.0  # unknown — not in memory files

    # Determine calibration bucket (which confidence decile the prediction fell into)
    # We don't have the actual prediction probability from memory files, so use a generic bucket
    calibration_bucket = 5  # middle bucket as fallback

    if not dry_run:
        db_path = str(BACKEND_DIR / "data" / "local_stage2.db")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        cur.execute("""
            INSERT INTO postmatch_eval
                (id, prediction_run_id, actual_home_goals, actual_away_goals,
                 actual_result, brier_score, log_loss, exact_score_hit, top3_hit,
                 calibration_bucket, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            eval_id, prediction_run_id, home_goals, away_goals,
            actual_result, brier, log_loss,
            False,  # exact_score_hit — unknown from memory
            False,  # top3_hit — unknown from memory
            calibration_bucket, notes, now,
        ))
        conn.commit()
        conn.close()

    return eval_id


def create_learning_log(
    match_id: str,
    prediction_run_id: str,
    snapshot_id: str,
    brier: float,
    components: dict,
    *,
    dry_run: bool = False,
) -> str:
    """Create a prediction_learning_log entry."""
    import sqlite3
    log_id = uuid.uuid4().hex[:32]

    # Determine error direction
    # Brier interpretation: < 0.15 excellent, 0.15-0.25 good, 0.25-0.5 fair, > 0.5 poor
    if brier < 0.15:
        error_direction = "well_calibrated"
    elif brier < 0.25:
        error_direction = "slightly_overconfident"
    elif brier < 0.5:
        error_direction = "overconfident"
    else:
        error_direction = "severely_overconfident"

    model_was_right = brier < 0.25  # rough threshold

    if not dry_run:
        db_path = str(BACKEND_DIR / "data" / "local_stage2.db")
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()

        cur.execute("""
            INSERT INTO prediction_learning_log
                (id, match_id, prediction_run_id, snapshot_id,
                 error_magnitude, error_direction,
                 dc_error_contribution, enhancer_error_contribution,
                 elo_error_contribution, signal_error_contribution,
                 market_error_contribution,
                 dc_marginal, enhancer_marginal, elo_marginal,
                 signal_marginal, market_marginal,
                 model_was_right, divergence_at_prediction,
                 context_tags, signal_verdicts, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            log_id, match_id, prediction_run_id, snapshot_id,
            brier, error_direction,
            0.0, 0.0, 0.0, 0.0, 0.0,  # error contributions — not in memory
            0.0, 0.0, 0.0, 0.0, 0.0,  # marginals — not in memory
            model_was_right, 0.0,  # divergence — not in memory
            json.dumps(["world_cup"]), json.dumps({}), "completed", now, now,
        ))
        conn.commit()
        conn.close()

    return log_id


def main():
    dry_run = "--dry-run" in sys.argv
    from_memory = "--from-memory" in sys.argv or "--all" in sys.argv
    auto_mode = "--auto" in sys.argv or "--all" in sys.argv
    rebuild_cal = "--rebuild-calibrator" in sys.argv or "--all" in sys.argv
    allow_placeholder_runs = "--allow-placeholder-runs" in sys.argv

    if not (from_memory or auto_mode or rebuild_cal or dry_run):
        print("Usage: backfill_postmatch_evals.py [--dry-run] [--from-memory] [--auto] [--rebuild-calibrator] [--all]")
        print("  --dry-run             Preview only, no DB writes")
        print("  --from-memory         Backfill 5 manual reviews from memory files")
        print("  --auto                Auto-backfill V3.8+ snapshots")
        print("  --rebuild-calibrator  Rebuild calibrator_wc.json from all WC evals")
        print("  --all                 All modes")
        print("  --allow-placeholder-runs  Legacy only: create placeholder prediction_runs when missing")
        return 1

    results = {"memory": {"found": 0, "created": 0, "skipped": 0, "no_snapshot": 0},
               "auto": {"found": 0, "created": 0, "skipped": 0}}

    # ── Memory mode ──
    if from_memory:
        print("=" * 60)
        print("PHASE 0a: Memory → DB backfill")
        print("=" * 60)

        for entry in MEMORY_EVALS:
            home = entry["home_team"]
            away = entry["away_team"]
            date = entry["match_date"]

            # Skip already-DB-covered matches
            if (home, away) in SKIP_MATCHES or (away, home) in SKIP_MATCHES:
                print(f"  SKIP: {home} vs {away} — already in DB postmatch_eval")
                results["memory"]["skipped"] += 1
                continue

            results["memory"]["found"] += 1

            # Find snapshot
            snap = find_snapshot(home, away, date)
            if not snap:
                print(f"  WARN: {home} vs {away} ({date}) — no snapshot found")
                results["memory"]["no_snapshot"] += 1
                continue

            # Find prediction_run
            prun_id = find_prediction_run(snap)
            if not prun_id:
                if not allow_placeholder_runs:
                    print(
                        f"  SKIP: {home} vs {away} — no prediction_run; "
                        "placeholder runs are disabled by default"
                    )
                    results["memory"]["skipped"] += 1
                    continue

                # Legacy escape hatch only. V4.9 learning/evaluation paths must
                # prefer real pre-match snapshots over synthetic probabilities.
                print(f"  LEGACY: {home} vs {away} — creating prediction_run placeholder")
                if not dry_run:
                    import sqlite3
                    db_path = str(BACKEND_DIR / "data" / "local_stage2.db")
                    conn = sqlite3.connect(db_path)
                    cur = conn.cursor()
                    prun_id = uuid.uuid4().hex[:32]
                    now = datetime.now(timezone.utc).isoformat()
                    empty_json = "{}"
                    cur.execute("""
                        INSERT INTO prediction_runs
                            (id, match_id, run_type, model_version, as_of_time,
                             home_win_prob, draw_prob, away_win_prob,
                             home_xg, away_xg, score_matrix, top3_scores,
                             confidence_score, risk_tags,
                             input_feature_snapshot, approved_signals, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        prun_id, snap["match_id"], "legacy_placeholder_backfill",
                        snap.get("model_version", "unknown"),
                        snap.get("match_time") or now,  # as_of_time
                        0.5, 0.25, 0.25,  # placeholder probs
                        1.5, 1.0,  # placeholder xG
                        empty_json, empty_json,  # score_matrix, top3_scores
                        0.7,  # confidence_score
                        empty_json,  # risk_tags
                        empty_json,  # input_feature_snapshot
                        empty_json,  # approved_signals
                        now,
                    ))
                    conn.commit()
                    conn.close()

            # Create postmatch_eval
            eval_id = create_postmatch_eval(
                prun_id, entry["actual_home_goals"], entry["actual_away_goals"],
                entry["brier_score"], entry.get("notes", ""),
                dry_run=dry_run,
            )

            # Create learning log
            log_id = create_learning_log(
                snap.get("match_id", ""), prun_id, snap["id"],
                entry["brier_score"], entry.get("components", {}),
                dry_run=dry_run,
            )

            tag = "[DRY RUN]" if dry_run else "[CREATED]"
            print(f"  {tag} {home} {entry['actual_home_goals']}-{entry['actual_away_goals']} {away} | "
                  f"Brier={entry['brier_score']:.4f} | eval={eval_id[:8]}... | log={log_id[:8]}...")
            results["memory"]["created"] += 1

        print(f"\nMemory backfill: {results['memory']['found']} found, "
              f"{results['memory']['created']} created, "
              f"{results['memory']['skipped']} skipped, "
              f"{results['memory']['no_snapshot']} no-snapshot")

    # ── Auto mode ──
    if auto_mode:
        print("\n" + "=" * 60)
        print("PHASE 0b: Auto-backfill V3.8+ snapshots")
        print("=" * 60)
        auto_backfill(dry_run=dry_run, results=results)

    if dry_run:
        print("\n*** DRY RUN — no changes written ***")

    # ── Rebuild calibrator ──
    if rebuild_cal:
        print("\n" + "=" * 60)
        print("PHASE 0c: Rebuild calibrator_wc.json")
        print("=" * 60)
        rebuild_wc_calibrator(dry_run=dry_run)

    return 0


def auto_backfill(*, dry_run: bool = False, results: dict | None = None):
    """Scan prediction_snapshots for V3.8+ WC matches and create evals."""
    import sqlite3
    db_path = str(BACKEND_DIR / "data" / "local_stage2.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Find V3.8+ WC snapshots
    cur.execute("""
        SELECT DISTINCT s.id, s.match_id, s.home_team, s.away_team, s.match_time,
               s.model_version, s.adjusted_probs
        FROM prediction_snapshots s
        WHERE (s.model_version LIKE '3.8%' OR s.model_version LIKE '3.9%'
               OR s.model_version LIKE '4.%')
          AND s.home_team IS NOT NULL
          AND s.away_team IS NOT NULL
        ORDER BY s.match_time
    """)
    snapshots = [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]

    # Get finished matches from wc26_schedule
    cur.execute("""
        SELECT home_team, away_team, match_date, home_goals, away_goals
        FROM wc26_schedule
        WHERE match_status = 'FINISHED' AND home_goals IS NOT NULL
    """)
    finished = {f"{r[0]}|{r[1]}": r for r in cur.fetchall()}

    count = 0
    for snap in snapshots:
        home = snap["home_team"]
        away = snap["away_team"]
        key = f"{home}|{away}"

        if key not in finished:
            # Try reversed
            rev_key = f"{away}|{home}"
            if rev_key in finished:
                key = rev_key
            else:
                continue

        match_row = finished[key]
        hg = match_row[3]
        ag = match_row[4]

        if hg is None or ag is None:
            continue

        # Check if eval already exists for this match_id
        cur2 = conn.cursor()
        cur2.execute("""
            SELECT COUNT(*) FROM postmatch_eval e
            JOIN prediction_runs r ON e.prediction_run_id = r.id
            WHERE r.match_id = ?
        """, (snap["match_id"],))
        if cur2.fetchone()[0] > 0:
            if results:
                results["auto"]["skipped"] += 1
            continue

        # Read probabilities from snapshot
        brier = compute_brier_from_snapshot(snap, hg, ag)
        if brier is None:
            continue

        results["auto"]["found"] += 1

        if not dry_run:
            # Create prediction_run
            prun_id = uuid.uuid4().hex[:32]
            now = datetime.now(timezone.utc).isoformat()
            cur3 = conn.cursor()
            try:
                raw = snap.get("adjusted_probs") if isinstance(snap.get("adjusted_probs"), str) else json.dumps(snap.get("adjusted_probs") or snap.get("probs") or {})
                raw_parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except (json.JSONDecodeError, TypeError):
                raw_parsed = {}
            # V4.2.1: handle both V4.x keys (home_win_prob/draw_prob/away_win_prob)
            # and V3.8 keys (home/draw/away) in snapshots
            probs = {
                "home_win_prob": float(raw_parsed.get("home_win_prob") or raw_parsed.get("home") or 0.5),
                "draw_prob": float(raw_parsed.get("draw_prob") or raw_parsed.get("draw") or 0.25),
                "away_win_prob": float(raw_parsed.get("away_win_prob") or raw_parsed.get("away") or 0.25),
            }
            cur3.execute("""
                INSERT INTO prediction_runs
                    (id, match_id, run_type, model_version, as_of_time,
                     home_win_prob, draw_prob, away_win_prob,
                     home_xg, away_xg, score_matrix, top3_scores,
                     confidence_score, risk_tags,
                     input_feature_snapshot, approved_signals, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                prun_id, snap["match_id"], "auto_backfill",
                snap.get("model_version", "unknown"),
                snap.get("match_time") or now,
                probs["home_win_prob"],
                probs["draw_prob"],
                probs["away_win_prob"],
                1.5, 1.0, "{}", "{}", 0.7, "{}", "{}", "{}", now,
            ))

            # Create eval inline (same connection)
            eval_id = uuid.uuid4().hex[:32]
            actual_result = "H" if hg > ag else ("A" if ag > hg else "D")
            cur3.execute("""
                INSERT INTO postmatch_eval
                    (id, prediction_run_id, actual_home_goals, actual_away_goals,
                     actual_result, brier_score, log_loss, exact_score_hit, top3_hit,
                     calibration_bucket, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                eval_id, prun_id, hg, ag, actual_result, brier, -1.0,
                False, False, 5,
                f"Auto-backfilled from snapshot {snap['model_version']}", now,
            ))

            # Create learning log inline (same connection)
            log_id = uuid.uuid4().hex[:32]
            model_was_right = brier < 0.25
            error_direction = "well_calibrated" if brier < 0.15 else (
                "slightly_overconfident" if brier < 0.25 else (
                "overconfident" if brier < 0.5 else "severely_overconfident"))
            cur3.execute("""
                INSERT INTO prediction_learning_log
                    (id, match_id, prediction_run_id, snapshot_id,
                     error_magnitude, error_direction,
                     dc_error_contribution, enhancer_error_contribution,
                     elo_error_contribution, signal_error_contribution,
                     market_error_contribution,
                     dc_marginal, enhancer_marginal, elo_marginal,
                     signal_marginal, market_marginal,
                     model_was_right, divergence_at_prediction,
                     context_tags, signal_verdicts, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                log_id, snap["match_id"], prun_id, snap["id"],
                brier, error_direction,
                0.0, 0.0, 0.0, 0.0, 0.0,
                0.0, 0.0, 0.0, 0.0, 0.0,
                model_was_right, 0.0,
                '["world_cup"]', "{}", "completed", now, now,
            ))

            conn.commit()

        count += 1
        tag = "[DRY RUN]" if dry_run else "[CREATED]"
        print(f"  {tag} {home} {hg}-{ag} {away} | Brier={brier:.4f} | v{snap['model_version']}")
        if results:
            results["auto"]["created"] += 1

    conn.commit()
    conn.close()

    if results:
        print(f"\nAuto backfill: {results['auto']['found']} found, "
              f"{results['auto']['created']} created, "
              f"{results['auto']['skipped']} skipped")


def compute_brier_from_snapshot(snap: dict, hg: int, ag: int) -> float | None:
    """Compute Brier score from a snapshot's probabilities and actual result."""
    try:
        probs_raw = snap.get("adjusted_probs") or snap.get("baseline_probs")
        if isinstance(probs_raw, str):
            probs = json.loads(probs_raw)
        elif isinstance(probs_raw, dict):
            probs = probs_raw
        else:
            return None
    except (json.JSONDecodeError, TypeError):
        return None

    # Handle multiple key formats: V3.8 uses {home, draw, away}, V4.x uses {home_win_prob, draw_prob, away_win_prob}
    p_home = probs.get("home_win_prob") or probs.get("home", 0.33)
    p_draw = probs.get("draw_prob") or probs.get("draw", 0.33)
    p_away = probs.get("away_win_prob") or probs.get("away", 0.33)

    # Sanity: if all are 0 or None, use uniform
    total = p_home + p_draw + p_away
    if total <= 0 or total > 2:
        p_home = p_draw = p_away = 0.3333
    else:
        # Normalize
        p_home /= total
        p_draw /= total
        p_away /= total

    # Actual outcomes as one-hot
    if hg > ag:
        o_home, o_draw, o_away = 1.0, 0.0, 0.0
    elif hg == ag:
        o_home, o_draw, o_away = 0.0, 1.0, 0.0
    else:
        o_home, o_draw, o_away = 0.0, 0.0, 1.0

    brier = ((p_home - o_home) ** 2 + (p_draw - o_draw) ** 2 + (p_away - o_away) ** 2) / 3.0
    return round(brier, 6)


def rebuild_wc_calibrator(*, dry_run: bool = False):
    """Rebuild calibrator_wc.json from all available WC postmatch_eval records.

    Uses pure-Python PAVA (Pool Adjacent Violators Algorithm) for isotonic
    regression — no numpy/scikit-learn dependency needed.
    """
    import sqlite3
    db_path = str(BACKEND_DIR / "data" / "local_stage2.db")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Collect WC eval records: include ALL run types with valid probabilities.
    # V4.2.2: Removed run_type filter — t_minus_24h, V4_MANUAL, PREMATCH etc.
    # all contain valid (prediction, outcome) pairs for calibration.
    # Only exclusion: placeholder probabilities (0.5/0.25/0.25).
    cur.execute("""
        SELECT e.actual_home_goals, e.actual_away_goals,
               r.home_win_prob, r.draw_prob, r.away_win_prob
        FROM postmatch_eval e
        JOIN prediction_runs r ON e.prediction_run_id = r.id
        WHERE e.actual_home_goals IS NOT NULL
          AND e.actual_away_goals IS NOT NULL
          AND r.home_win_prob IS NOT NULL
          AND r.draw_prob IS NOT NULL
          AND r.away_win_prob IS NOT NULL
          AND NOT (r.home_win_prob = 0.5 AND r.draw_prob = 0.25 AND r.away_win_prob = 0.25)
    """)
    records = []
    for row in cur.fetchall():
        hg, ag, ph, pd, pa = row
        if ph is None and pd is None and pa is None:
            continue

        actual = "H" if hg > ag else ("A" if ag > hg else "D")
        records.append({
            "home_win_prob": ph,
            "draw_prob": pd,
            "away_win_prob": pa,
            "actual_result": actual,
        })
    conn.close()

    print(f"  Collected {len(records)} eval records for WC calibrator")

    if len(records) < 15:
        print(f"  WARNING: Only {len(records)} records — need >=15 for fitting. "
              "Calibrator will not be fitted.")
        return

    # Pure-Python isotonic regression (PAVA) for each outcome
    CAL_KEYS = {"home_win": "home_win_prob", "draw": "draw_prob", "away_win": "away_win_prob"}
    calibrators = {}
    ece_values = []

    for outcome_key, prob_field in CAL_KEYS.items():
        # Build (predicted_prob, actual_binary) pairs, sorted by predicted prob
        pairs = []
        for rec in records:
            pred = rec[prob_field]
            actual = 1.0 if (
                (rec["actual_result"] == "H" and outcome_key == "home_win") or
                (rec["actual_result"] == "D" and outcome_key == "draw") or
                (rec["actual_result"] == "A" and outcome_key == "away_win")
            ) else 0.0
            pairs.append((pred, actual))

        pairs.sort(key=lambda x: x[0])

        if len(pairs) < 2:
            continue

        # PAVA: pool adjacent violators
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]

        # Simple PAVA implementation
        blocks = [(xs[i], ys[i], 1.0) for i in range(len(xs))]  # (sum_x, sum_y, weight)

        # Forward pass: merge blocks where y values decrease
        i = 0
        while i < len(blocks) - 1:
            mean_i = blocks[i][1] / blocks[i][2]
            mean_j = blocks[i + 1][1] / blocks[i + 1][2]
            if mean_i > mean_j:
                # Merge blocks i and i+1 (sum accumulated x, y, weight)
                new_sum_x = blocks[i][0] + blocks[i + 1][0]
                new_sum_y = blocks[i][1] + blocks[i + 1][1]
                new_w = blocks[i][2] + blocks[i + 1][2]
                blocks[i] = (new_sum_x, new_sum_y, new_w)
                blocks.pop(i + 1)
                # Go back one step if possible to check for new violations
                if i > 0:
                    i -= 1
            else:
                i += 1

        # Extract threshold points from blocks (mean x, mean y for each block)
        x_thresholds = []
        y_thresholds = []
        for block in blocks:
            mean_x = block[0] / block[2]  # sum_x / weight
            mean_y = block[1] / block[2]  # sum_y / weight
            x_thresholds.append(round(mean_x, 6))
            y_thresholds.append(round(min(1.0, max(0.0, mean_y)), 6))

        calibrators[outcome_key] = {
            "x_thresholds": x_thresholds,
            "y_thresholds": y_thresholds,
        }

        # Compute ECE for this outcome
        ece = 0.0
        for block in blocks:
            pred_mean = block[0] / block[2]  # avg predicted prob in this bin
            actual_rate = block[1] / block[2]  # avg actual outcome rate
            weight = block[2] / len(pairs)
            ece += abs(pred_mean - actual_rate) * weight
        ece_values.append(ece)

    is_fitted = any(calibrators.values())
    avg_ece = sum(ece_values) / len(ece_values) if ece_values else 0.0
    from datetime import datetime, timezone
    fitted_at = datetime.now(timezone.utc).isoformat() if is_fitted else None

    payload = {
        "is_fitted": is_fitted,
        "fitted_at": fitted_at,
        "training_sample_count": len(records),
        "expected_calibration_error": round(avg_ece, 6),
        "calibrators": {
            key: {
                "x_thresholds": cal["x_thresholds"],
                "y_thresholds": cal["y_thresholds"],
            }
            if cal else None
            for key, cal in calibrators.items()
        },
    }

    if dry_run:
        print(f"  [DRY RUN] Would save calibrator_wc.json: "
              f"fitted={is_fitted}, samples={len(records)}, "
              f"ece={avg_ece:.4f}")
    else:
        import json
        cal_path = str(BACKEND_DIR / "artifacts" / "calibrator_wc.json")
        with open(cal_path, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"  Saved: calibrator_wc.json (fitted={is_fitted}, "
              f"samples={len(records)}, ece={avg_ece:.4f})")


if __name__ == "__main__":
    sys.exit(main())
