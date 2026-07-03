"""Bootstrap uncertainty quantification for DC-based match predictions.

Computes confidence intervals for predicted probabilities by:
1. Bootstrapping the training match data (resample WITH replacement)
2. Recomputing team attack/defense parameters for each bootstrap sample
3. Propagating uncertainty through to final probabilities
4. Reporting 50% and 95% confidence intervals

The bootstrap accounts for:
- Parameter estimation uncertainty (finite training data)
- Match-level variability (resampling matches captures goal randomness)

Usage (standalone):
    python scripts/_bootstrap_ci.py "England" "Croatia"
"""
from __future__ import annotations

import io
import json
import math
import random
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    from app.services.prediction_core import _load_dc, _load_training_df
    from app.services.prediction_timer import PredictionTimer
except ImportError:
    print("ERROR: Cannot import prediction_core. Run from project root.")
    sys.exit(1)


def extract_team_match_data(team_name: str, training_df) -> dict:
    """Extract all match data for a specific team from the training DataFrame."""
    home_matches = training_df[training_df['home_team'] == team_name]
    away_matches = training_df[training_df['away_team'] == team_name]

    return {
        "home_goals_for": home_matches['home_goals'].values if len(home_matches) > 0 else np.array([]),
        "home_goals_against": home_matches['away_goals'].values if len(home_matches) > 0 else np.array([]),
        "home_opponents": home_matches['away_team'].values if len(home_matches) > 0 else np.array([]),
        "away_goals_for": away_matches['away_goals'].values if len(away_matches) > 0 else np.array([]),
        "away_goals_against": away_matches['home_goals'].values if len(away_matches) > 0 else np.array([]),
        "away_opponents": away_matches['home_team'].values if len(away_matches) > 0 else np.array([]),
        "n_home": len(home_matches),
        "n_away": len(away_matches),
        "n_total": len(home_matches) + len(away_matches),
    }


def bootstrap_lambda_ci(home_team: str, away_team: str, is_neutral: bool = True,
                         n_bootstrap: int = 1000, seed: int = 42) -> dict:
    """Bootstrap confidence intervals for DC-predicted Poisson lambdas.

    Strategy: parametric bootstrap of the training match-level goal rates.
    For each bootstrap iteration:
    1. Resample the training matches with replacement
    2. Recompute attack/defense parameters
    3. Compute predicted lambdas for the target match
    4. Convert to outcome probabilities
    """
    random.seed(seed)
    np.random.seed(seed)

    timer = PredictionTimer()
    dc = _load_dc(timer)
    df = _load_training_df(timer)

    # Extract match data for both teams
    home_data = extract_team_match_data(home_team, df)
    away_data = extract_team_match_data(away_team, df)

    print(f"  {home_team}: {home_data['n_total']} matches ({home_data['n_home']}H + {home_data['n_away']}A)")
    print(f"  {away_team}: {away_data['n_total']} matches ({away_data['n_home']}H + {away_data['n_away']}A)")

    # Get base prediction for comparison
    base_pred = dc.predict_match(home_team, away_team, is_neutral_venue=is_neutral)
    base_hxg = base_pred['home_xg']
    base_axg = base_pred['away_xg']
    print(f"\n  Base prediction: xG {home_team}={base_hxg:.3f} / {away_team}={base_axg:.3f}")

    # ── Bootstrap approach ──
    # We use a semi-parametric bootstrap:
    # 1. For each team's attack parameter, we estimate the standard error
    #    by jackknifing (leave-one-out) the training matches
    # 2. For each bootstrap iteration, we draw attack/defense from
    #    the approximate normal distribution with that SE
    # 3. We use the current DC home_advantage and opponent averaging

    # Compute jackknife estimates for each team
    def jackknife_team_goals(team: str) -> dict:
        """Jackknife estimate of a team's goal-scoring rate uncertainty."""
        team_home_matches = df[df['home_team'] == team]
        team_away_matches = df[df['away_team'] == team]

        # Home goals for
        h_gf = team_home_matches['home_goals'].values
        # Home goals against
        h_ga = team_home_matches['away_goals'].values
        # Away goals for
        a_gf = team_away_matches['away_goals'].values
        # Away goals against
        a_ga = team_away_matches['home_goals'].values

        results = {}
        for label, goals in [("home_for", h_gf), ("home_against", h_ga),
                              ("away_for", a_gf), ("away_against", a_ga)]:
            if len(goals) >= 5:
                n = len(goals)
                mean = float(np.mean(goals))
                # Jackknife SE
                jk_means = []
                for i in range(n):
                    jk = np.delete(goals, i)
                    jk_means.append(float(np.mean(jk)))
                jk_means = np.array(jk_means)
                se = math.sqrt((n - 1) * np.mean((jk_means - np.mean(jk_means)) ** 2))
                results[label] = {"mean": mean, "se": se, "n": n}
            else:
                results[label] = {"mean": float(np.mean(goals)) if len(goals) > 0 else 0.0,
                                  "se": 0.5, "n": len(goals), "unreliable": True}

        return results

    home_jk = jackknife_team_goals(home_team)
    away_jk = jackknife_team_goals(away_team)

    # ── Semi-parametric bootstrap ──
    # For each iteration:
    # 1. Resample training matches WITH REPLACEMENT
    # 2. Compute team-level home/away goal rates (empirical means)
    # 3. Convert to xG predictions using the DC model's match-level structure
    # 4. Key: we DON'T try to re-fit DC — we bootstrap the GOAL RATES and
    #    use the base DC model to translate rates into λs for the specific matchup
    #
    # The uncertainty comes from: "how much do the team's goal rates vary
    # if we resample their match history?"

    bootstrap_results = []
    n_failed = 0

    # Compute opponent pool averages for normalization
    # (these stay fixed — only team-specific rates are bootstrapped)
    league_avg_goals = float(df['home_goals'].mean())

    for i in range(n_bootstrap):
        try:
            # Resample training matches WITH REPLACEMENT
            bs_df = df.sample(n=len(df), replace=True, random_state=seed + i)

            # ── Home team: goals scored & conceded ──
            bs_h_home = bs_df[bs_df['home_team'] == home_team]
            bs_h_away = bs_df[bs_df['away_team'] == home_team]
            n_h_h = len(bs_h_home)
            n_h_a = len(bs_h_away)

            # Simple fallback: if bootstrap sample doesn't contain enough of this team,
            # use the original rates (this happens rarely with large bootstraps)
            h_gf = float(bs_h_home['home_goals'].mean()) if n_h_h >= 3 else home_jk['home_for']['mean']
            h_ga = float(bs_h_home['away_goals'].mean()) if n_h_h >= 3 else home_jk['home_against']['mean']
            h_agf = float(bs_h_away['away_goals'].mean()) if n_h_a >= 3 else home_jk['away_for']['mean']
            h_aga = float(bs_h_away['home_goals'].mean()) if n_h_a >= 3 else home_jk['away_against']['mean']

            # ── Away team: goals scored & conceded ──
            bs_a_home = bs_df[bs_df['home_team'] == away_team]
            bs_a_away = bs_df[bs_df['away_team'] == away_team]
            n_a_h = len(bs_a_home)
            n_a_a = len(bs_a_away)

            a_gf = float(bs_a_home['home_goals'].mean()) if n_a_h >= 3 else away_jk['home_for']['mean']
            a_ga = float(bs_a_home['away_goals'].mean()) if n_a_h >= 3 else away_jk['home_against']['mean']
            a_agf = float(bs_a_away['away_goals'].mean()) if n_a_a >= 3 else away_jk['away_for']['mean']
            a_aga = float(bs_a_away['home_goals'].mean()) if n_a_a >= 3 else away_jk['away_against']['mean']

            # Use the DC model's own attack/defense decomposition to translate
            # bootstrapped goal rates into matchup-specific xG predictions.
            #
            # DC model: log(λ_H) = α_H(attack) - β_A(defense) + γ(home_adv)
            # We use the team's bootstrapped rate to derive a rate-relative attack/defense,
            # multiplied against the opponent's base DC parameter to get the matchup λ.
            #
            # Simpler approach: use bootstrapped goal rates DIRECTLY as empirical λ estimates.
            # λ_H ≈ (team GF rate at home) × (opponent GA rate away) / (league avg GF)
            # This is the standard "goal expectancy" formula.

            # League average from bootstrap sample
            bs_league_avg = float(bs_df['home_goals'].mean())  # same as away goals mean
            if bs_league_avg < 0.1:
                bs_league_avg = league_avg_goals

            # Empirical xG using rate-product formula:
            # λ_H = (home_team_home_GF_rate + home_team_away_GF_rate)/2 ×
            #        (away_team_home_GA_rate + away_team_away_GA_rate)/2 / league_avg
            h_atk_rate = (h_gf + h_agf) / 2.0  # team's overall GF rate
            h_def_rate = (h_ga + h_aga) / 2.0  # team's overall GA rate
            a_atk_rate = (a_gf + a_agf) / 2.0
            a_def_rate = (a_ga + a_aga) / 2.0

            # Simple multiplicative λ estimate
            # λ_H ∝ home_atk × away_def / league_avg
            lambda_h = h_atk_rate * a_def_rate / max(bs_league_avg, 0.25)
            lambda_a = a_atk_rate * h_def_rate / max(bs_league_avg, 0.25)

            # Apply home advantage if not neutral (rough ~15% boost)
            if not is_neutral:
                lambda_h *= 1.15
                lambda_a *= 0.92

            # Clamp to reasonable range (0.1 to 6.0 goals)
            lambda_h = max(0.10, min(6.0, lambda_h))
            lambda_a = max(0.10, min(6.0, lambda_a))

            # Convert to probabilities via independent Poisson (20x20 grid)
            prob_h = 0.0
            prob_d = 0.0
            prob_a = 0.0
            for h in range(20):
                ph = lambda_h ** h * math.exp(-lambda_h) / math.factorial(h)
                for ag in range(20):
                    pa = lambda_a ** ag * math.exp(-lambda_a) / math.factorial(ag)
                    p = ph * pa
                    if h > ag:
                        prob_h += p
                    elif h == ag:
                        prob_d += p
                    else:
                        prob_a += p

            bootstrap_results.append({
                "lambda_h": lambda_h,
                "lambda_a": lambda_a,
                "prob_h": prob_h,
                "prob_d": prob_d,
                "prob_a": prob_a,
            })

        except Exception:
            n_failed += 1
            continue

    if len(bootstrap_results) < 100:
        print(f"  ERROR: Only {len(bootstrap_results)} valid bootstrap samples (need ≥100)")
        return {}

    # ── Compute confidence intervals ──
    lambdas_h = np.array([r["lambda_h"] for r in bootstrap_results])
    lambdas_a = np.array([r["lambda_a"] for r in bootstrap_results])
    probs_h = np.array([r["prob_h"] for r in bootstrap_results])
    probs_d = np.array([r["prob_d"] for r in bootstrap_results])
    probs_a = np.array([r["prob_a"] for r in bootstrap_results])

    def percentile_ci(arr: np.ndarray, is_prob: bool = True) -> dict:
        scale = 100 if is_prob else 1
        return {
            "median": round(float(np.percentile(arr, 50)) * scale, 1),
            "mean": round(float(np.mean(arr)) * scale, 1),
            "ci50_low": round(float(np.percentile(arr, 25)) * scale, 1),
            "ci50_high": round(float(np.percentile(arr, 75)) * scale, 1),
            "ci95_low": round(float(np.percentile(arr, 2.5)) * scale, 1),
            "ci95_high": round(float(np.percentile(arr, 97.5)) * scale, 1),
            "std": round(float(np.std(arr)) * scale, 1),
            "raw_median": float(np.percentile(arr, 50)),
            "raw_mean": float(np.mean(arr)),
            "raw_ci95": [float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5))],
        }

    result = {
        "home_team": home_team,
        "away_team": away_team,
        "is_neutral": is_neutral,
        "n_bootstrap": len(bootstrap_results),
        "n_failed": n_failed,
        "seed": seed,
        "base_xg": {"home": base_hxg, "away": base_axg},
        "bootstrap_xg": {
            "home": percentile_ci(lambdas_h, is_prob=False),
            "away": percentile_ci(lambdas_a, is_prob=False),
        },
        "home_win": percentile_ci(probs_h),
        "draw": percentile_ci(probs_d),
        "away_win": percentile_ci(probs_a),
        "bootstrap_distribution": {
            "home_win": {
                "percentiles": {
                    "5": round(float(np.percentile(probs_h, 5)) * 100, 1),
                    "10": round(float(np.percentile(probs_h, 10)) * 100, 1),
                    "25": round(float(np.percentile(probs_h, 25)) * 100, 1),
                    "50": round(float(np.percentile(probs_h, 50)) * 100, 1),
                    "75": round(float(np.percentile(probs_h, 75)) * 100, 1),
                    "90": round(float(np.percentile(probs_h, 90)) * 100, 1),
                    "95": round(float(np.percentile(probs_h, 95)) * 100, 1),
                }
            },
            "draw": {
                "percentiles": {
                    "5": round(float(np.percentile(probs_d, 5)) * 100, 1),
                    "10": round(float(np.percentile(probs_d, 10)) * 100, 1),
                    "25": round(float(np.percentile(probs_d, 25)) * 100, 1),
                    "50": round(float(np.percentile(probs_d, 50)) * 100, 1),
                    "75": round(float(np.percentile(probs_d, 75)) * 100, 1),
                    "90": round(float(np.percentile(probs_d, 90)) * 100, 1),
                    "95": round(float(np.percentile(probs_d, 95)) * 100, 1),
                }
            },
            "away_win": {
                "percentiles": {
                    "5": round(float(np.percentile(probs_a, 5)) * 100, 1),
                    "10": round(float(np.percentile(probs_a, 10)) * 100, 1),
                    "25": round(float(np.percentile(probs_a, 25)) * 100, 1),
                    "50": round(float(np.percentile(probs_a, 50)) * 100, 1),
                    "75": round(float(np.percentile(probs_a, 75)) * 100, 1),
                    "90": round(float(np.percentile(probs_a, 90)) * 100, 1),
                    "95": round(float(np.percentile(probs_a, 95)) * 100, 1),
                }
            },
        },
    }

    # ── How does bootstrap compare to base? ──
    base_h_prob = dc_pred_to_probs(base_hxg, base_axg)
    result["base_prediction"] = {
        "home_win": round(base_h_prob["home"] * 100, 1),
        "draw": round(base_h_prob["draw"] * 100, 1),
        "away_win": round(base_h_prob["away"] * 100, 1),
    }

    # Compute overlap between base and bootstrap CI
    for key in ["home_win", "draw", "away_win"]:
        base_val = result["base_prediction"][key] / 100
        ci = result[key]["raw_ci95"]
        in_ci = ci[0] <= base_val <= ci[1]
        result[key]["base_in_95ci"] = in_ci

    return result


def dc_pred_to_probs(hxg: float, axg: float, max_g: int = 20) -> dict:
    """Convert DC xG to H/D/A probabilities via independent Poisson."""
    prob_h = 0.0
    prob_d = 0.0
    prob_a = 0.0
    for h in range(max_g):
        for a in range(max_g):
            p = (hxg ** h * math.exp(-hxg) / math.factorial(h)) * \
                (axg ** a * math.exp(-axg) / math.factorial(a))
            if h > a:
                prob_h += p
            elif h == a:
                prob_d += p
            else:
                prob_a += p
    return {"home": prob_h, "draw": prob_d, "away": prob_a}


def format_probability_ci(ci_data: dict) -> str:
    """Format probability with CI: '43% [35%, 51%]' """
    if not ci_data:
        return "N/A"
    med = ci_data["median"]
    ci50_l = ci_data["ci50_low"]
    ci50_h = ci_data["ci50_high"]
    ci95_l = ci_data["ci95_low"]
    ci95_h = ci_data["ci95_high"]
    return f"{med:.0f}% (50% CI: {ci50_l:.0f}%–{ci50_h:.0f}%, 95% CI: {ci95_l:.0f}%–{ci95_h:.0f}%)"


def _can_use_box_drawing() -> bool:
    """Check whether the current stdout supports Unicode box-drawing."""
    return sys.stdout.encoding and sys.stdout.encoding.upper() in (
        "UTF-8", "UTF8", "UTF_8", "UTF-16", "UTF-16-LE", "UTF-16-BE",
    )


def print_bootstrap_summary(result: dict):
    """Print a human-readable summary of bootstrap results."""
    if not result:
        print("  No results to print.")
        return

    use_unicode = _can_use_box_drawing()

    print(f"\n{'='*70}")
    print(f"BOOTSTRAP UNCERTAINTY QUANTIFICATION")
    print(f"  {result['home_team']} vs {result['away_team']} (neutral={result['is_neutral']})")
    print(f"  Bootstrap samples: {result['n_bootstrap']} (failed: {result['n_failed']})")
    print(f"{'='*70}")

    if use_unicode:
        print(f"\n  ╔{'═'*12}╤{'═'*10}╤{'═'*48}╗")
        print(f"  ║  Outcome      │  Point   │  95% Bootstrap CI                          ║")
        print(f"  ╠{'═'*12}╪{'═'*10}╪{'═'*48}╣")
    else:
        sep = "+" + "-"*12 + "+" + "-"*10 + "+" + "-"*48 + "+"
        print(f"\n  {sep}")
        print(f"  |  Outcome      |  Point   |  95% Bootstrap CI                          |")
        print(f"  |{'='*12}|{'='*10}|{'='*48}|")

    for key, label in [("home_win", f"{result['home_team']} win"),
                        ("draw", "Draw"),
                        ("away_win", f"{result['away_team']} win")]:
        ci = result[key]
        base = result["base_prediction"][key]
        in_ci = "Y" if ci.get("base_in_95ci", False) else "N"
        if use_unicode:
            print(f"  ║ {label:12s} │ {base:5.1f}%   │ [{ci['ci95_low']:5.1f}%, {ci['ci95_high']:5.1f}%]  "
                  f"(base in CI: {in_ci})  ║")
        else:
            print(f"  | {label:12s} | {base:5.1f}%   | [{ci['ci95_low']:5.1f}%, {ci['ci95_high']:5.1f}%]  "
                  f"(base in CI: {in_ci})  |")

    if use_unicode:
        print(f"  ╚{'═'*12}╧{'═'*10}╧{'═'*48}╝")
    else:
        print(f"  +{'-'*12}+{'-'*10}+{'-'*48}+")

    # xG uncertainty
    print(f"\n  xG Bootstrap (95% CI):")
    for key, team in [("home", result['home_team']), ("away", result['away_team'])]:
        bsg = result["bootstrap_xg"][key]
        base_xg = result["base_xg"][key]
        ci_lo = bsg['ci95_low']
        ci_hi = bsg['ci95_high']
        print(f"    {team}: {base_xg:.2f} [{ci_lo:.2f} – {ci_hi:.2f}]")

    # Distribution shape
    print(f"\n  Home win percentile distribution:")
    hw = result["bootstrap_distribution"]["home_win"]["percentiles"]
    print(f"    P5={hw['5']}%  P25={hw['25']}%  P50={hw['50']}%  P75={hw['75']}%  P95={hw['95']}%")
    print(f"    Spread (P95-P5): {float(hw['95']) - float(hw['5']):.1f}pp")


def main():
    home = sys.argv[1] if len(sys.argv) > 1 else "England"
    away = sys.argv[2] if len(sys.argv) > 2 else "Croatia"
    neutral = "--neutral" in sys.argv or "-n" in sys.argv
    n_iter = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 1000

    print("=" * 70)
    print("BOOTSTRAP CONFIDENCE INTERVALS — Match Prediction")
    print(f"  {home} vs {away} | Neutral: {neutral} | Iterations: {n_iter}")
    print("=" * 70)

    print(f"\n[1] Extracting team match data...")
    result = bootstrap_lambda_ci(home, away, is_neutral=neutral, n_bootstrap=n_iter)

    if result:
        print_bootstrap_summary(result)

        # Save result
        out_path = BACKEND_DIR / "data" / f"_bootstrap_{home.replace(' ','_')}_{away.replace(' ','_')}.json"
        with open(str(out_path), "w", encoding="utf-8") as f:
            # Remove raw arrays for file size
            clean = {k: v for k, v in result.items() if k != "bootstrap_samples_raw"}
            json.dump(clean, f, indent=2, default=str, ensure_ascii=False)
        print(f"\n  ✅ Saved: {out_path.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
