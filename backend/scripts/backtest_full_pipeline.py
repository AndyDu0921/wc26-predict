#!/usr/bin/env python3
"""backtest_full_pipeline.py — Full-pipeline walk-forward backtest.

Verifies that 180d half-life remains optimal when all downstream
fusion components (Enhancer → Elo → Pi → DrawFloor) are included.

Tests 4 half-life values: [30, 60, 90, 180]
Across 17 walk-forward windows covering 58 WC26 matches with results.

Methodology
-----------
For each unique match-date window (chronological):
  1. Train DC on all pre-window data with each half-life → DC probs
  2. Compute Elo ratings incrementally from pre-window history → Elo probs
  3. Compute Pi ratings incrementally from pre-window history → Pi probs
  4. Fuse: DC → Enhancer(skip) → Elo → Pi (sequential, WC weights)
  5. Apply draw floor (≥12%)
  6. Evaluate fused probs against actual results

Metrics: Brier, LogLoss, Direction Accuracy, Draw Brier,
         RPS (Ranked Probability Score), ECE (Expected Calibration Error)

Usage:
    python scripts/backtest_full_pipeline.py
    python scripts/backtest_full_pipeline.py --halflife 60,90,180
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import numpy as np
import pandas as pd

from app.services.dixon_coles import DixonColesModel
from app.core.engine import run_core_fusion, enforce_draw_floor, CoreFusionResult
from app.core.backtest_helpers import (
    DEFAULT_ELO, ELO_HOME_ADVANTAGE, ELO_K_FACTOR, DC_HALF_LIFE,
    WC26_COMPETITION, KO_STAGES,
    load_all_training_data, load_wc26_eval_matches,
    compute_elo_probs, update_elo_ratings, build_elo_ratings_as_of,
    compute_pi_probs, update_pi_ratings, build_pi_ratings_as_of,
)

# ── Paths (script-local) ──
DB_PATH = BACKEND_DIR / "data" / "local_stage2.db"
ARTIFACTS_DIR = BACKEND_DIR / "artifacts"
ELO_RATINGS_PATH = ARTIFACTS_DIR / "ratings" / "elo.json"
PI_RATINGS_PATH = ARTIFACTS_DIR / "ratings" / "pi.json"
CONFIG_DIR = BACKEND_DIR / "app" / "configs"
OUTPUT_PATH = CONFIG_DIR / "full_pipeline_backtest_results.json"

# ── Grid ──
DEFAULT_HALF_LIVES = [30, 60, 90, 180]

# ── WC weights (from V4.3.1) ──
WC_DC_WEIGHT = 0.90
WC_ELO_WEIGHT = 0.12
WC_PI_WEIGHT = 0.17
# Enhancer skipped in backtest (0.10 weight, 23% accuracy → noise floor)
# Weibull skipped (30% failure rate, independent of DC half-life)
# Market skipped (no historical market data for backtest)

# ── Thin wrappers around shared backtest helpers ──
# load_all_training_data / load_wc26_eval_matches are imported from
# app.core.backtest_helpers — re-export under the old function signatures
# so existing callsites don't need to be rewritten.

def load_all_training_data(min_date: str = "2020-01-01") -> pd.DataFrame:
    """Load finished national-team matches (→ shared helper)."""
    from app.core.backtest_helpers import load_all_training_data as _load
    return _load(DB_PATH, min_date=min_date)

def load_wc26_eval_matches() -> pd.DataFrame:
    """Load WC26 evaluation matches (→ shared helper)."""
    from app.core.backtest_helpers import load_wc26_eval_matches as _load_eval
    return _load_eval(DB_PATH)


# ═══════════════════════════════════════════════════════════════════════
#  Evaluation helpers
# ═══════════════════════════════════════════════════════════════════════

def compute_brier(probs: dict[str, float], actual_idx: int) -> float:
    """Brier score for a single 3-outcome prediction."""
    prob_vec = np.array([
        probs.get("home_win_prob", probs.get("home", 0.33)),
        probs.get("draw_prob", probs.get("draw", 0.33)),
        probs.get("away_win_prob", probs.get("away", 0.33)),
    ], dtype=float)
    actual = np.zeros(3)
    actual[actual_idx] = 1.0
    return float(((prob_vec - actual) ** 2).sum())


def compute_rps(probs: dict[str, float], actual_idx: int) -> float:
    """Ranked Probability Score for 3-outcome prediction."""
    prob_vec = np.array([
        probs.get("home_win_prob", probs.get("home", 0.33)),
        probs.get("draw_prob", probs.get("draw", 0.33)),
        probs.get("away_win_prob", probs.get("away", 0.33)),
    ], dtype=float)
    actual = np.zeros(3)
    actual[actual_idx] = 1.0

    cum_probs = np.cumsum(prob_vec)
    cum_actual = np.cumsum(actual)
    return float(((cum_probs - cum_actual) ** 2).sum() / 2.0)


def compute_ece(probs_list: list[np.ndarray], actuals: list[int], n_bins: int = 10) -> float:
    """Expected Calibration Error."""
    if not probs_list:
        return 0.0
    confidences = []
    correct = []
    for p_vec, act in zip(probs_list, actuals):
        confidences.append(float(p_vec[act]))
        predicted_class = int(np.argmax(p_vec))
        correct.append(1.0 if predicted_class == act else 0.0)

    conf = np.array(confidences)
    corr = np.array(correct)
    bin_edges = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    total = len(conf)
    for i in range(n_bins):
        mask = (conf >= bin_edges[i]) & (conf < bin_edges[i + 1])
        if mask.sum() > 0:
            bin_acc = corr[mask].mean()
            bin_conf = conf[mask].mean()
            ece += (mask.sum() / total) * abs(bin_acc - bin_conf)
    return float(ece)


def determine_actual(home_goals: int, away_goals: int) -> int:
    """Return 0=home win, 1=draw, 2=away win."""
    if home_goals > away_goals:
        return 0
    elif home_goals == away_goals:
        return 1
    return 2


def compute_scoreline_logloss(
    score_matrix: list[list[float]],
    home_goals: int, away_goals: int,
    max_goals: int = 5,
) -> float:
    """LogLoss for the exact scoreline prediction from the score matrix."""
    if not score_matrix:
        return float("nan")
    g = min(max_goals, len(score_matrix) - 1)
    if home_goals <= g and away_goals <= g:
        prob = float(score_matrix[home_goals][away_goals])
        return float(-math.log(max(prob, 1e-12)))
    return float(-math.log(1e-12))


def evaluate_fused(
    dc_model: DixonColesModel,
    match_row,
    elo_ratings: dict[str, float],
    pi_ratings: dict[str, float],
) -> dict[str, Any]:
    """Evaluate one match: DC probs → fusion → draw floor → metrics."""
    home_team = match_row.home_team
    away_team = match_row.away_team
    is_neutral = bool(match_row.is_neutral_venue)
    stage = match_row.stage
    home_goals = int(match_row.home_goals)
    away_goals = int(match_row.away_goals)
    actual_idx = determine_actual(home_goals, away_goals)

    # ── DC prediction ──
    dc_pred = dc_model.predict_match(home_team, away_team, is_neutral_venue=is_neutral)
    dc_home_xg = float(dc_pred.get("home_xg", 1.0))
    dc_away_xg = float(dc_pred.get("away_xg", 1.0))

    # ── Elo prediction ──
    is_ko = stage in KO_STAGES
    kappa = 0.30  # WC kappa
    elo_probs = compute_elo_probs(home_team, away_team, elo_ratings, is_neutral, kappa)

    # ── Pi prediction ──
    pi_probs = compute_pi_probs(home_team, away_team, pi_ratings, is_neutral)

    # ── Core fusion (skip Enhancer, skip Weibull — both independent of DC) ──
    fusion_result = run_core_fusion(
        dc_probs={
            "home_win_prob": float(dc_pred["home_win_prob"]),
            "draw_prob": float(dc_pred["draw_prob"]),
            "away_win_prob": float(dc_pred["away_win_prob"]),
        },
        dc_home_xg=dc_home_xg,
        dc_away_xg=dc_away_xg,
        dc_base_weight=WC_DC_WEIGHT,
        enh_probs=None,        # skip Enhancer (weight 0.10 → effective ~0.07)
        weibull_probs=None,    # skip Weibull (30% failure rate)
        weibull_weight=0.0,
        elo_probs=elo_probs,
        elo_weight=WC_ELO_WEIGHT,
        pi_probs=pi_probs,
        pi_weight=WC_PI_WEIGHT,
    )

    # ── Draw floor enforcement ──
    fused_after_floor, _draw_floor_applied = enforce_draw_floor(dict(fusion_result.probs))

    # ── Metrics ──
    dc_brier = compute_brier(dc_pred, actual_idx)
    fused_brier = compute_brier(fused_after_floor, actual_idx)
    dc_logloss = float(-math.log(max(
        [float(dc_pred["home_win_prob"]), float(dc_pred["draw_prob"]), float(dc_pred["away_win_prob"])][actual_idx],
        1e-12,
    )))
    fused_logloss = float(-math.log(max(
        [fused_after_floor["home_win_prob"], fused_after_floor["draw_prob"], fused_after_floor["away_win_prob"]][actual_idx],
        1e-12,
    )))
    rps = compute_rps(fused_after_floor, actual_idx)

    # Direction accuracy
    dc_direction = int(np.argmax([
        float(dc_pred["home_win_prob"]), float(dc_pred["draw_prob"]), float(dc_pred["away_win_prob"])
    ]))
    fused_direction = int(np.argmax([
        fused_after_floor["home_win_prob"], fused_after_floor["draw_prob"], fused_after_floor["away_win_prob"]
    ]))

    # Draw Brier (only for actual draws)
    draw_brier = None
    if actual_idx == 1:
        draw_actual = np.array([0.0, 1.0, 0.0])
        draw_prob_vec = np.array([
            fused_after_floor["home_win_prob"],
            fused_after_floor["draw_prob"],
            fused_after_floor["away_win_prob"],
        ])
        draw_brier = float(((draw_prob_vec - draw_actual) ** 2).sum())

    # Scoreline LogLoss from DC matrix
    score_matrix = dc_pred.get("score_matrix", [])
    sll = compute_scoreline_logloss(score_matrix, home_goals, away_goals)

    return {
        "match": f"{home_team} vs {away_team}",
        "date": str(match_row.match_date.date()),
        "stage": stage,
        "actual": f"{home_goals}-{away_goals}",
        "actual_idx": actual_idx,
        "dc_brier": round(dc_brier, 6),
        "fused_brier": round(fused_brier, 6),
        "dc_logloss": round(dc_logloss, 6),
        "fused_logloss": round(fused_logloss, 6),
        "rps": round(rps, 6),
        "draw_brier": round(draw_brier, 6) if draw_brier is not None else None,
        "scoreline_logloss": round(sll, 6),
        "dc_direction_correct": dc_direction == actual_idx,
        "fused_direction_correct": fused_direction == actual_idx,
        "dc_probs": {
            "home": round(float(dc_pred["home_win_prob"]), 4),
            "draw": round(float(dc_pred["draw_prob"]), 4),
            "away": round(float(dc_pred["away_win_prob"]), 4),
        },
        "fused_probs": {
            "home": round(fused_after_floor["home_win_prob"], 4),
            "draw": round(fused_after_floor["draw_prob"], 4),
            "away": round(fused_after_floor["away_win_prob"], 4),
        },
    }


def aggregate_metrics(details: list[dict]) -> dict[str, Any]:
    """Aggregate per-match metrics into a summary."""
    n = len(details)
    if n == 0:
        return {"n_matches": 0}

    dc_briers = [d["dc_brier"] for d in details]
    fused_briers = [d["fused_brier"] for d in details]
    dc_loglosses = [d["dc_logloss"] for d in details]
    fused_loglosses = [d["fused_logloss"] for d in details]
    rps_scores = [d["rps"] for d in details]
    sll_vals = [d["scoreline_logloss"] for d in details if not math.isnan(d["scoreline_logloss"])]

    dc_dir = sum(1 for d in details if d["dc_direction_correct"])
    fused_dir = sum(1 for d in details if d["fused_direction_correct"])

    # Draw Brier
    draw_details = [d for d in details if d["draw_brier"] is not None]
    draw_brier_mean = float(np.mean([d["draw_brier"] for d in draw_details])) if draw_details else None

    # ECE
    probs_list = [
        np.array([
            d["fused_probs"]["home"], d["fused_probs"]["draw"], d["fused_probs"]["away"]
        ])
        for d in details
    ]
    actuals = [d["actual_idx"] for d in details]
    ece = compute_ece(probs_list, actuals)

    # By stage breakdown
    group_details = [d for d in details if d["stage"] not in KO_STAGES]
    ko_details = [d for d in details if d["stage"] in KO_STAGES]

    return {
        "n_matches": n,
        "dc_brier": float(np.mean(dc_briers)),
        "fused_brier": float(np.mean(fused_briers)),
        "dc_logloss": float(np.mean(dc_loglosses)),
        "fused_logloss": float(np.mean(fused_loglosses)),
        "rps": float(np.mean(rps_scores)),
        "ece": round(ece, 6),
        "draw_brier": round(draw_brier_mean, 6) if draw_brier_mean is not None else None,
        "draw_match_count": len(draw_details),
        "scoreline_logloss": float(np.mean(sll_vals)) if sll_vals else None,
        "dc_direction_accuracy": dc_dir / n,
        "fused_direction_accuracy": fused_dir / n,
        "dc_direction_counts": {"correct": dc_dir, "wrong": n - dc_dir},
        "fused_direction_counts": {"correct": fused_dir, "wrong": n - fused_dir},
        "group_stage": {
            "n": len(group_details),
            "dc_brier": float(np.mean([d["dc_brier"] for d in group_details])) if group_details else None,
            "fused_brier": float(np.mean([d["fused_brier"] for d in group_details])) if group_details else None,
            "fused_direction": sum(1 for d in group_details if d["fused_direction_correct"]) / len(group_details) if group_details else None,
        } if group_details else None,
        "knockout": {
            "n": len(ko_details),
            "dc_brier": float(np.mean([d["dc_brier"] for d in ko_details])) if ko_details else None,
            "fused_brier": float(np.mean([d["fused_brier"] for d in ko_details])) if ko_details else None,
            "draw_brier": float(np.mean([d["draw_brier"] for d in ko_details if d["draw_brier"] is not None])) if ko_details else None,
            "fused_direction": sum(1 for d in ko_details if d["fused_direction_correct"]) / len(ko_details) if ko_details else None,
        } if ko_details else None,
    }


# ═══════════════════════════════════════════════════════════════════════
#  Main backtest loop
# ═══════════════════════════════════════════════════════════════════════

def run_full_pipeline_backtest(
    train_df: pd.DataFrame,
    eval_df: pd.DataFrame,
    half_lives: list[int],
) -> list[dict[str, Any]]:
    """Run full-pipeline walk-forward backtest for each half-life.

    For each unique match-date window:
      1. Pre-compute Elo & Pi ratings (independent of DC half-life)
      2. For each half-life: train DC → get probs → fuse → evaluate
    """
    unique_dates = sorted(eval_df["match_date"].dt.normalize().unique())
    n_windows = len(unique_dates)
    n_candidates = len(half_lives)
    print(f"  Walk-forward over {n_windows} windows × {n_candidates} half-lives")
    print(f"  Total: {n_windows * n_candidates} DC fits + {n_windows} Elo/Pi builds")
    print()

    # Pre-compute Elo/Pi ratings for each window (shared across half-lives)
    window_ratings: dict[pd.Timestamp, tuple[dict, dict]] = {}
    print("  Pre-computing Elo & Pi ratings per window...")
    for date_val in unique_dates:
        elo_r = build_elo_ratings_as_of(train_df, date_val)
        pi_r = build_pi_ratings_as_of(train_df, date_val)
        window_ratings[date_val] = (elo_r, pi_r)
        n_teams = len(elo_r)
        print(f"    {date_val.date()}: {n_teams} teams rated")
    print()

    # Results per half-life
    all_results: list[dict[str, Any]] = []

    for hl_idx, hl in enumerate(half_lives):
        t0 = time.perf_counter()
        print(f"  [{hl_idx+1}/{n_candidates}] half_life={hl}d:", flush=True)

        all_details: list[dict] = []
        total_dc_fit_time = 0.0

        for date_val in unique_dates:
            day_matches = eval_df[eval_df["match_date"].dt.normalize() == date_val]
            elo_ratings, pi_ratings = window_ratings[date_val]

            # Train DC on pre-window data
            train_cut = train_df[train_df["match_date"].dt.normalize() < date_val]
            if len(train_cut) < 100:
                print(f"    WARN {date_val.date()}: only {len(train_cut)} training rows, skipping")
                continue

            t_fit = time.perf_counter()
            model = DixonColesModel(half_life_days=hl)
            model.fit(train_cut)
            dc_fit_time = time.perf_counter() - t_fit
            total_dc_fit_time += dc_fit_time

            # Evaluate all matches on this date
            for match_row in day_matches.itertuples(index=False):
                eval_result = evaluate_fused(model, match_row, elo_ratings, pi_ratings)
                eval_result["half_life"] = hl
                all_details.append(eval_result)

            # Progress
            n_matches = len(day_matches)
            day_dir = sum(
                1 for d in all_details[-n_matches:]
                if d["fused_direction_correct"]
            )
            bar = "#" if day_dir >= n_matches / 2 else "."
            print(
                f"    {date_val.date()}  {n_matches} matches  "
                f"Dir={day_dir}/{n_matches}  "
                f"DC_fit={dc_fit_time:.1f}s  {bar}",
                flush=True,
            )

        elapsed = time.perf_counter() - t0
        metrics = aggregate_metrics(all_details)

        print(
            f"  -> DC_Brier={metrics['dc_brier']:.4f}  "
            f"Fused_Brier={metrics['fused_brier']:.4f}  "
            f"DC_Dir={metrics['dc_direction_accuracy']:.1%}  "
            f"Fused_Dir={metrics['fused_direction_accuracy']:.1%}  "
            f"RPS={metrics['rps']:.4f}  "
            f"ECE={metrics['ece']:.4f}  "
            f"({elapsed:.1f}s total, {total_dc_fit_time:.1f}s DC fits)",
            flush=True,
        )

        all_results.append({
            "half_life_days": hl,
            "metrics": metrics,
            "n_matches_evaluated": metrics["n_matches"],
            "walk_forward_windows": n_windows,
            "total_time_s": round(elapsed, 1),
            "dc_fit_time_s": round(total_dc_fit_time, 1),
            "details": all_details,
        })

    return all_results


# ═══════════════════════════════════════════════════════════════════════
#  Output formatting
# ═══════════════════════════════════════════════════════════════════════

def print_comparison_table(results: list[dict[str, Any]]) -> None:
    """Print comprehensive comparison table."""
    print(f"\n{'='*100}")
    print(f"  FULL-PIPELINE WALK-FORWARD BACKTEST RESULTS")
    print(f"  DC → Elo → Pi → DrawFloor (WC weights: DC={WC_DC_WEIGHT}, "
          f"Elo={WC_ELO_WEIGHT}, Pi={WC_PI_WEIGHT})")
    print(f"{'='*100}")

    # Sort by fused Brier (lower is better)
    sorted_results = sorted(results, key=lambda r: r["metrics"]["fused_brier"])

    header = (
        f"{'Rank':<5} {'HL(d)':<8} {'DC_Brier':<10} {'Fused_Brier':<12} "
        f"{'DC_Dir':<8} {'Fused_Dir':<10} {'RPS':<8} {'ECE':<8} "
        f"{'Draw_Brier':<12} {'SLine_LL':<10} {'Time(s)':<8}"
    )
    print(header)
    print("-" * len(header))

    for rank, r in enumerate(sorted_results, 1):
        m = r["metrics"]
        draw_brier_str = f"{m['draw_brier']:.4f}" if m.get("draw_brier") is not None else "N/A"
        sll_str = f"{m['scoreline_logloss']:.4f}" if m.get("scoreline_logloss") is not None else "N/A"
        print(
            f"{rank:<5} {r['half_life_days']:<8} "
            f"{m['dc_brier']:<10.4f} {m['fused_brier']:<12.4f} "
            f"{m['dc_direction_accuracy']:<8.1%} {m['fused_direction_accuracy']:<10.1%} "
            f"{m['rps']:<8.4f} {m['ece']:<8.4f} "
            f"{draw_brier_str:<12} {sll_str:<10} "
            f"{r.get('total_time_s', 0):<8.1f}"
        )

    best = sorted_results[0]
    current_hl = next((r for r in results if r["half_life_days"] == 180), None)
    print(f"\n  Best fused:  half_life={best['half_life_days']}d  "
          f"Fused_Brier={best['metrics']['fused_brier']:.4f}  "
          f"DC_Brier={best['metrics']['dc_brier']:.4f}")
    if current_hl:
        current_rank = next(
            i + 1 for i, r in enumerate(sorted_results)
            if r["half_life_days"] == 180
        )
        dbrier = current_hl["metrics"]["fused_brier"] - best["metrics"]["fused_brier"]
        print(f"  Current (180d): rank={current_rank}  "
              f"ΔFused_Brier={'+' if dbrier > 0 else ''}{dbrier:.4f} "
              f"({'worse' if dbrier > 0 else 'better' if dbrier < 0 else 'tied'})")

    # Per-stage breakdown for best
    gs = best["metrics"].get("group_stage")
    ko = best["metrics"].get("knockout")
    if gs:
        print(f"\n  Best group-stage ({gs['n']} matches):  "
              f"Fused_Brier={gs['fused_brier']:.4f}  "
              f"Fused_Dir={gs['fused_direction']:.1%}" if gs.get("fused_direction") else "")
    if ko:
        print(f"  Best knockout ({ko['n']} matches):     "
              f"Fused_Brier={ko['fused_brier']:.4f}  "
              f"Fused_Dir={ko['fused_direction']:.1%}" if ko.get("fused_direction") else "")

    print(f"\n{'='*100}")

    # Delta analysis: how much does fusion change DC?
    print(f"\n  Fusion effect (DC → Fused):")
    for r in sorted_results:
        m = r["metrics"]
        dbrier = m["fused_brier"] - m["dc_brier"]
        ddir = m["fused_direction_accuracy"] - m["dc_direction_accuracy"]
        print(
            f"    {r['half_life_days']}d:  "
            f"ΔBrier={'+' if dbrier > 0 else ''}{dbrier:.4f}  "
            f"ΔDir={'+' if ddir > 0 else ''}{ddir:+.1%}"
        )
    print()


def save_results(results: list[dict[str, Any]]) -> None:
    """Save full backtest results as JSON."""
    # Strip per-match details for compact storage
    compact = []
    for r in results:
        stripped = dict(r)
        stripped["details"] = [
            {k: v for k, v in d.items() if k != "actual_idx"}
            for d in r["details"]
        ]
        compact.append(stripped)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "description": "Full-pipeline walk-forward backtest: DC → Elo → Pi → DrawFloor",
        "half_lives_tested": [r["half_life_days"] for r in results],
        "weights_used": {
            "dc": WC_DC_WEIGHT,
            "elo": WC_ELO_WEIGHT,
            "pi": WC_PI_WEIGHT,
            "note": "Enhancer and Weibull skipped (independent of DC half-life)",
        },
        "results": compact,
    }

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  Results saved to: {OUTPUT_PATH}")


# ═══════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Full-pipeline walk-forward backtest with DC half-life grid",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--halflife",
        type=str,
        default=None,
        help="Comma-separated half-life values (default: 30,60,90,180)",
    )
    args = parser.parse_args()

    if args.halflife:
        half_lives = [int(x.strip()) for x in args.halflife.split(",")]
    else:
        half_lives = DEFAULT_HALF_LIVES

    print("=" * 80)
    print("  FULL-PIPELINE WALK-FORWARD BACKTEST")
    print("  DC → Elo → Pi → DrawFloor")
    print("=" * 80)
    print(f"  Half-lives: {half_lives}")
    print()

    # -- Load data --
    print("-- Loading data --")
    train_df = load_all_training_data()
    eval_df = load_wc26_eval_matches()

    # -- Run backtest --
    print(f"\n-- Running backtest --")
    results = run_full_pipeline_backtest(train_df, eval_df, half_lives)

    # -- Output --
    print_comparison_table(results)
    save_results(results)

    print("Done.")


if __name__ == "__main__":
    main()
