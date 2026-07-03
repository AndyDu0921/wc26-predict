#!/usr/bin/env python3
"""Backfill prediction_learning_log for matches where process_eval exists but learning_log is missing.

Usage: python backend/scripts/backfill_learning_logs.py [--match-ids 189 190 191]
"""

import sqlite3
import json
import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent.parent / "data" / "local_stage2.db"


def get_component_prob(comp_probs: dict, key: str) -> dict | None:
    """Extract {home, draw, away} from component_probs by key."""
    data = comp_probs.get(key)
    if data is None:
        return None
    return {"home": float(data["home"]), "draw": float(data["draw"]), "away": float(data["away"])}


def brier(probs: dict, actual: str) -> float:
    """Compute Brier score given {home, draw, away} probs and actual result ('home'|'draw'|'away')."""
    oh = {"home": (1, 0, 0), "draw": (0, 1, 0), "away": (0, 0, 1)}[actual]
    return (probs["home"] - oh[0]) ** 2 + (probs["draw"] - oh[1]) ** 2 + (probs["away"] - oh[2]) ** 2


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    match_ids = sys.argv[2:] if len(sys.argv) > 2 else ["189", "190", "191"]

    # Validate args are integers
    try:
        match_ids_int = [int(m) for m in match_ids]
    except ValueError:
        print(f"ERROR: match-ids must be integers, got: {match_ids}")
        sys.exit(1)

    for mid in match_ids_int:
        print(f"\n{'='*60}")
        print(f"Processing match_id={mid}")

        # 1. Get snapshot
        cur.execute(
            "SELECT * FROM pre_match_snapshots WHERE match_id=? ORDER BY snapshot_at DESC LIMIT 1",
            (mid,),
        )
        snap = cur.fetchone()
        if not snap:
            print(f"  SKIP: No snapshot found for match_id={mid}")
            continue

        snap_dict = dict(snap)
        snapshot_id = snap_dict.get("id", "")
        comp_probs = json.loads(snap_dict.get("component_probs") or "{}")
        final_home = float(snap_dict.get("final_home_prob", 0))
        final_draw = float(snap_dict.get("final_draw_prob", 0))
        final_away = float(snap_dict.get("final_away_prob", 0))

        # 2. Get actual result
        cur.execute(
            "SELECT home_goals, away_goals, home_team, away_team FROM wc26_schedule WHERE id=?",
            (mid,),
        )
        sched = cur.fetchone()
        if not sched:
            print(f"  SKIP: No schedule row for match_id={mid}")
            continue
        home_goals = int(sched["home_goals"] or 0)
        away_goals = int(sched["away_goals"] or 0)
        if home_goals > away_goals:
            actual = "home"
        elif home_goals < away_goals:
            actual = "away"
        else:
            actual = "draw"
        print(f"  Result: {sched['home_team']} {home_goals}-{away_goals} {sched['away_team']} ({actual})")

        # 3. Get process_eval
        cur.execute(
            "SELECT * FROM postmatch_process_eval WHERE match_id=? ORDER BY created_at DESC LIMIT 1",
            (mid,),
        )
        peval = cur.fetchone()
        if peval:
            pe_dict = dict(peval)
            lw = float(pe_dict.get("learning_weight", 1.0))
            failure_type = pe_dict.get("model_failure_type", "UNKNOWN")
        else:
            lw = 1.0
            failure_type = "UNKNOWN"
            print("  WARNING: No process_eval record, using LW=1.0")

        # Determine learning_tier
        if lw >= 0.70:
            tier = "full"
        elif lw >= 0.30:
            tier = "diagnostic"
        else:
            tier = "record_only"

        status = "active" if lw >= 0.30 else "record_only"

        print(f"  LW={lw:.3f} tier={tier} failure={failure_type}")
        print(f"  Fusion: H={final_home:.4f} D={final_draw:.4f} A={final_away:.4f}")

        # 4. Compute component-level Brier scores and marginals
        fusion_probs = {"home": final_home, "draw": final_draw, "away": final_away}
        fusion_brier_val = brier(fusion_probs, actual)
        model_was_right = (actual == max(fusion_probs, key=fusion_probs.get))

        print(f"  Fusion Brier: {fusion_brier_val:.4f}  model_right={model_was_right}")

        # Key components to extract
        component_keys = {
            "dixon_coles": "dc",
            "enhancer": "enhancer",
            "dixon_coles+enhancer": "dc_enhancer",
            "weibull": "weibull",
            "elo": "elo",
            "pi_rating": "pi",
            "negbin": "negbin",
        }

        component_briers = {}
        component_marginals = {}
        for comp_key, short_name in component_keys.items():
            probs = get_component_prob(comp_probs, comp_key)
            if probs is None:
                continue
            cb = brier(probs, actual)
            marginal = fusion_brier_val - cb  # positive = component better than fusion
            component_briers[short_name] = cb
            component_marginals[short_name] = round(marginal, 4)
            direction = max(probs, key=probs.get)
            correct = "OK" if direction == actual else "XX"
            print(f"    {short_name:15s}: H={probs['home']:.4f} D={probs['draw']:.4f} A={probs['away']:.4f} "
                  f"Brier={cb:.4f} marginal={marginal:+.4f} dir={correct}")

        # 5. Map to DB columns
        dc_marginal = component_marginals.get("dc")
        enhancer_marginal = component_marginals.get("enhancer")
        elo_marginal = component_marginals.get("elo")
        signal_marginal = component_marginals.get("pi")  # Pi-Rating = signal
        # Weibull is a key component - store in market_marginal slot since market is unused
        weibull_marginal = component_marginals.get("weibull")
        market_marginal = 0.0  # Market was not blended

        # Error contributions = -marginal (negative marginal means component was worse than fusion)
        dc_error_contrib = round(-dc_marginal, 4) if dc_marginal is not None else None
        enhancer_error_contrib = round(-enhancer_marginal, 4) if enhancer_marginal is not None else None
        elo_error_contrib = round(-elo_marginal, 4) if elo_marginal is not None else None
        signal_error_contrib = round(-signal_marginal, 4) if signal_marginal is not None else None
        market_error_contrib = 0.0

        # Build context_tags with extra components that don't have dedicated columns
        context_tags = {
            "weibull_marginal": weibull_marginal,
            "negbin_marginal": component_marginals.get("negbin"),
            "dc_enhancer_marginal": component_marginals.get("dc_enhancer"),
            "weibull_brier": component_briers.get("weibull"),
            "negbin_brier": component_briers.get("negbin"),
            "dc_enhancer_brier": component_briers.get("dc_enhancer"),
            "model_failure_type": failure_type,
        }

        # Compute error_direction - what did the model predict as max?
        error_direction = max(fusion_probs, key=fusion_probs.get)

        # Compute divergence_at_prediction
        model_disagreement = snap_dict.get("model_disagreement")
        if model_disagreement is not None:
            try:
                divergence = float(model_disagreement)
            except (ValueError, TypeError):
                divergence = 0.0
        else:
            divergence = 0.0

        # Generate a deterministic hex ID (32 chars, no dashes)
        log_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"backfill-learning-log-{mid}").hex

        # 6. Check if entry already exists
        cur.execute(
            "SELECT id FROM prediction_learning_log WHERE match_id=? AND snapshot_id=?",
            (str(mid), snapshot_id),
        )
        existing = cur.fetchone()
        if existing:
            print(f"  Entry already exists (id={existing[0]}), updating...")
            cur.execute(
                """UPDATE prediction_learning_log SET
                    error_magnitude=?, error_direction=?, model_was_right=?,
                    dc_marginal=?, enhancer_marginal=?, elo_marginal=?,
                    signal_marginal=?, market_marginal=?,
                    dc_error_contribution=?, enhancer_error_contribution=?,
                    elo_error_contribution=?, signal_error_contribution=?,
                    market_error_contribution=?,
                    divergence_at_prediction=?, context_tags=?,
                    learning_weight=?, learning_tier=?, status=?,
                    updated_at=?
                WHERE id=?""",
                (
                    round(fusion_brier_val, 4),
                    error_direction,
                    model_was_right,
                    dc_marginal,
                    enhancer_marginal,
                    elo_marginal,
                    signal_marginal,
                    market_marginal,
                    dc_error_contrib,
                    enhancer_error_contrib,
                    elo_error_contrib,
                    signal_error_contrib,
                    market_error_contrib,
                    divergence,
                    json.dumps(context_tags),
                    lw,
                    tier,
                    status,
                    datetime.now(timezone.utc).isoformat(),
                    existing[0],
                ),
            )
        else:
            cur.execute(
                """INSERT INTO prediction_learning_log (
                    id, match_id, snapshot_id, prediction_run_id,
                    error_magnitude, error_direction,
                    dc_error_contribution, enhancer_error_contribution,
                    elo_error_contribution, signal_error_contribution,
                    market_error_contribution,
                    model_was_right, divergence_at_prediction,
                    context_tags, signal_verdicts,
                    dc_marginal, enhancer_marginal, elo_marginal,
                    signal_marginal, market_marginal,
                    learning_weight, learning_tier, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    log_id,
                    str(mid),
                    snapshot_id,
                    round(fusion_brier_val, 4),
                    error_direction,
                    dc_error_contrib,
                    enhancer_error_contrib,
                    elo_error_contrib,
                    signal_error_contrib,
                    market_error_contrib,
                    model_was_right,
                    divergence,
                    json.dumps(context_tags),
                    dc_marginal,
                    enhancer_marginal,
                    elo_marginal,
                    signal_marginal,
                    market_marginal,
                    lw,
                    tier,
                    status,
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            print(f"  INSERTED id={log_id}")

    conn.commit()
    conn.close()
    print(f"\n{'='*60}")
    print("Done. All changes committed.")


if __name__ == "__main__":
    main()
