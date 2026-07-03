"""V4.7-score: Grid search for optimal score prediction hyperparameters.

Walk-forward backtest across completed WC26 matches to find the best
combination of:

  - WC_XG_CALIBRATION_FACTOR (xG multiplier)
  - NEGBIN_R (dispersion parameter)
  - NEGBIN_FUSION_WEIGHT (NegBin influence in H/D/A fusion)
  - tau_rho (Dixon-Coles dependence parameter)

Evaluation metrics:
  - Score Log Loss (primary — Wheatcroft 2021 recommended)
  - Exact Hit Rate
  - Top-3 Hit Rate
  - Top-5 Hit Rate

Usage:
  python backend/scripts/grid_search_score_params.py [--db-path PATH] [--top-n 10]
"""

from __future__ import annotations

import math
import sqlite3
import sys
from itertools import product
from pathlib import Path
from typing import Any

# ── Paths ──
BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB = BACKEND_DIR / "data" / "local_stage2.db"

# ── Parameter grid ──
PARAM_GRID = {
    "xG_factor": [1.0, 1.2, 1.35, 1.5, 1.8],
    "negbin_r": [1.5, 2.5, 3.5, 5.0, 8.0],
    "tau_rho": [-0.30, -0.20, -0.15, -0.10, 0.0],
}

# ── NumPy-free NegBin PMF (copied from engine.py for zero-IO) ──


def negbin_pmf(k: int, mu: float, r: float) -> float:
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    p = r / (r + mu)
    log_prob = r * math.log(p)
    for i in range(k):
        log_prob += math.log(r + i) - math.log(i + 1)
    log_prob += k * math.log(1 - p)
    return math.exp(log_prob)


def negbin_score_matrix_grid(hxg, axg, max_g, r, xG_factor, tau_rho):
    """Standalone NegBin score matrix function for grid search (no imports)."""
    hxg_cal = hxg * xG_factor
    axg_cal = axg * xG_factor

    matrix = [[0.0] * (max_g + 1) for _ in range(max_g + 1)]
    for h in range(max_g + 1):
        ph = negbin_pmf(h, hxg_cal, r)
        for a in range(max_g + 1):
            pa = negbin_pmf(a, axg_cal, r)
            matrix[h][a] = ph * pa

    # Normalise
    total = sum(sum(row) for row in matrix)
    if total > 0:
        for h in range(max_g + 1):
            for a in range(max_g + 1):
                matrix[h][a] /= total

    # τ correction
    if tau_rho is not None:
        factors = {
            (0, 0): 1 - (hxg_cal * axg_cal * tau_rho),
            (0, 1): 1 + (hxg_cal * tau_rho),
            (1, 0): 1 + (axg_cal * tau_rho),
            (1, 1): 1 - tau_rho,
        }
        for (h, a), factor in factors.items():
            if h <= max_g and a <= max_g:
                matrix[h][a] = max(matrix[h][a] * factor, 1e-12)
        total = sum(sum(row) for row in matrix)
        if total > 0:
            for h in range(max_g + 1):
                for a in range(max_g + 1):
                    matrix[h][a] /= total

    return matrix


# ── Metrics ──


def score_log_loss(matrix, hg, ag):
    """Log Score — Wheatcroft (2021) recommended proper scoring rule."""
    if hg < len(matrix) and ag < len(matrix[0]):
        p = max(matrix[hg][ag], 1e-12)
    else:
        p = 1e-12
    return -math.log(p)


def exact_hit(matrix, hg, ag):
    """Did the top-1 score match?"""
    flat = [(matrix[h][a], h, a) for h in range(len(matrix)) for a in range(len(matrix[0]))]
    top = sorted(flat, reverse=True)[0]
    return 1 if top[1] == hg and top[2] == ag else 0


def top_n_hit(matrix, hg, ag, n=3):
    """Was the actual score in top-N?"""
    flat = [(matrix[h][a], h, a) for h in range(len(matrix)) for a in range(len(matrix[0]))]
    top = sorted(flat, reverse=True)[:n]
    return 1 if any(t[1] == hg and t[2] == ag for t in top) else 0


# ── Data loading ──


def load_completed_matches(db_path: str) -> list[dict[str, Any]]:
    """Load WC26 matches with known results and DC xG from snapshots."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT s.id, s.home_team, s.away_team,
                   s.home_goals, s.away_goals, s.match_date, s.stage
            FROM wc26_schedule s
            WHERE s.match_status = 'FINISHED'
              AND s.home_goals IS NOT NULL
              AND s.away_goals IS NOT NULL
              AND s.home_team IS NOT NULL
              AND s.away_team IS NOT NULL
            ORDER BY s.match_date
        """)
        rows = cur.fetchall()

        matches = []
        for row in rows:
            # Get the DC xG from pre_match_snapshots
            cur.execute("""
                SELECT home_xg, away_xg
                FROM pre_match_snapshots
                WHERE match_id = ?
                ORDER BY snapshot_at DESC
                LIMIT 1
            """, (str(row[0]),))
            snap = cur.fetchone()

            # Fall back to postmatch_process_eval for predicted xG
            predicted_home_xg = None
            predicted_away_xg = None
            if snap and snap[0] is not None:
                predicted_home_xg = float(snap[0])
                predicted_away_xg = float(snap[1])
            else:
                # Try postmatch_process_eval
                cur.execute("""
                    SELECT predicted_home_xg, predicted_away_xg
                    FROM postmatch_process_eval
                    WHERE match_id = ?
                """, (row[0],))
                pe = cur.fetchone()
                if pe and pe[0] is not None:
                    predicted_home_xg = float(pe[0])
                    predicted_away_xg = float(pe[1])

            if predicted_home_xg is not None and predicted_away_xg is not None:
                matches.append({
                    "id": row[0],
                    "home_team": row[1],
                    "away_team": row[2],
                    "home_goals": int(row[3]),
                    "away_goals": int(row[4]),
                    "match_date": row[5],
                    "stage": row[6],
                    "home_xg": predicted_home_xg,
                    "away_xg": predicted_away_xg,
                })

        return matches
    finally:
        conn.close()


# ── Main ──


def main(db_path: str, top_n_param: int = 10):
    matches = load_completed_matches(db_path)
    if not matches:
        print("No completed matches with xG data found.")
        return

    print(f"Loaded {len(matches)} completed matches with xG data\n")
    print("Parameter grid:")
    for key, values in PARAM_GRID.items():
        print(f"  {key}: {values}")
    print(f"  Total combinations: {math.prod(len(v) for v in PARAM_GRID.values())}")
    print()

    # Build all combinations
    keys = list(PARAM_GRID.keys())
    values = list(PARAM_GRID.values())
    all_combos = list(product(*values))

    results = []
    for combo in all_combos:
        params = dict(zip(keys, combo))

        total_log_loss = 0.0
        exact = 0
        top3 = 0
        top5 = 0
        n_eval = 0

        for m in matches:
            hxg = m["home_xg"]
            axg = m["away_xg"]
            hg = m["home_goals"]
            ag = m["away_goals"]

            if hxg <= 0 or axg <= 0:
                continue

            matrix = negbin_score_matrix_grid(
                hxg, axg, max_g=5,
                r=params["negbin_r"],
                xG_factor=params["xG_factor"],
                tau_rho=params["tau_rho"],
            )

            total_log_loss += score_log_loss(matrix, hg, ag)
            exact += exact_hit(matrix, hg, ag)
            top3 += top_n_hit(matrix, hg, ag, n=3)
            top5 += top_n_hit(matrix, hg, ag, n=5)
            n_eval += 1

        if n_eval > 0:
            avg_log_loss = total_log_loss / n_eval
            exact_rate = exact / n_eval * 100
            top3_rate = top3 / n_eval * 100
            top5_rate = top5 / n_eval * 100

            results.append({
                "params": params,
                "avg_log_loss": avg_log_loss,
                "exact_rate": exact_rate,
                "top3_rate": top3_rate,
                "top5_rate": top5_rate,
                "n": n_eval,
            })

    # Sort by log loss (lower is better)
    results.sort(key=lambda x: x["avg_log_loss"])

    print("=" * 90)
    print(f"{'Rank':<5} {'LogLoss':<9} {'Exact%':<8} {'Top3%':<8} {'Top5%':<8} {'N':<5}  Parameters")
    print("-" * 90)

    # Baseline: current parameters
    current_params = {"xG_factor": 1.35, "negbin_r": 3.5, "tau_rho": -0.15}
    current_rank = None

    for rank, r in enumerate(results[:top_n_param], 1):
        p = r["params"]
        marker = ""
        if all(abs(p[k] - current_params[k]) < 1e-9 for k in current_params):
            marker = " ← CURRENT"
            current_rank = rank
        print(
            f"{rank:<5} {r['avg_log_loss']:<9.4f} {r['exact_rate']:<8.1f} "
            f"{r['top3_rate']:<8.1f} {r['top5_rate']:<8.1f} {r['n']:<5}"
            f"  xG={p['xG_factor']:.2f} r={p['negbin_r']:.1f} "
            f"ρ={p['tau_rho']:.2f}{marker}"
        )

    print("-" * 90)
    if current_rank:
        print(f"Current params rank: #{current_rank}/{len(results)}")
    else:
        print("Current params (xG=1.35 r=3.5 ρ=-0.15) not in grid — "
              "add to grid if needed")

    # Also show the DC-only baseline
    print()
    print("DC-only baseline (Poisson+tau with original xG):")
    dc_params = {"xG_factor": 1.0, "negbin_r": 100.0, "tau_rho": -0.15}
    # Use a very large r to approximate Poisson
    dc_total_ll = 0.0
    dc_exact = 0
    dc_top3 = 0
    dc_top5 = 0
    dc_n = 0
    for m in matches:
        hxg, axg = m["home_xg"], m["away_xg"]
        hg, ag = m["home_goals"], m["away_goals"]
        if hxg <= 0 or axg <= 0:
            continue
        matrix = negbin_score_matrix_grid(hxg, axg, 5, 100.0, 1.0, -0.15)
        dc_total_ll += score_log_loss(matrix, hg, ag)
        dc_exact += exact_hit(matrix, hg, ag)
        dc_top3 += top_n_hit(matrix, hg, ag, n=3)
        dc_top5 += top_n_hit(matrix, hg, ag, n=5)
        dc_n += 1
    if dc_n > 0:
        print(f"  LogLoss={dc_total_ll/dc_n:.4f} Exact={dc_exact/dc_n*100:.1f}% "
              f"Top3={dc_top3/dc_n*100:.1f}% Top5={dc_top5/dc_n*100:.1f}% N={dc_n}")

    # Print best recommendation
    if results:
        best = results[0]
        bp = best["params"]
        print()
        print("=" * 90)
        print("RECOMMENDED PARAMETERS:")
        print(f"  WC_XG_CALIBRATION_FACTOR = {bp['xG_factor']:.2f}")
        print(f"  NEGBIN_R               = {bp['negbin_r']:.1f}")
        print(f"  tau_rho (DC ρ)         = {bp['tau_rho']:.2f}")
        print(f"  Expected LogLoss: {best['avg_log_loss']:.4f}")
        print(f"  Expected Exact%:  {best['exact_rate']:.1f}%")
        print(f"  Expected Top3%:   {best['top3_rate']:.1f}%")
        print(f"  Expected Top5%:   {best['top5_rate']:.1f}%")
        print("=" * 90)


if __name__ == "__main__":
    db_path = str(DEFAULT_DB)
    top_n_param = 10
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--db-path" and i + 1 < len(args):
            db_path = args[i + 1]
            i += 2
        elif args[i] == "--top-n" and i + 1 < len(args):
            top_n_param = int(args[i + 1])
            i += 2
        else:
            i += 1
    main(db_path, top_n_param)
