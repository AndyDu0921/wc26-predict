"""Pure probability-fusion helpers with zero IO dependencies.

All functions are deterministic: same inputs → same outputs. No DB, no
network, no file reads, no model loading. Shared helpers in this module are
used by the CLI, API, Dashboard, and Tournament Simulator paths.

Fusion chain: DC → Enhancer → NegBin → Weibull → Elo → Pi → Market → DrawFloor
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ── Constants ───────────────────────────────────────────────────

WC_XG_CALIBRATION_FACTOR = 1.35  # WC xG calibration (1.20→1.35: KO stage 50-80% underestimation, 2026-07-04 R32 review)
NEGBIN_R = 8.0  # Grid search optimal on 10 completed WC26 matches
NEGBIN_FUSION_WEIGHT = 0.05  # NegBin influence in sequential fusion
MARKET_BOOST_ATTENUATION = 0.60
MARKET_BOOST_DC_ENH_DIVERGENCE_PP = 15.0
MARKET_BOOST_DIVERGENCE_THRESHOLD = 0.15  # model-market divergence triggers boost
MARKET_BOOST_MAX = 0.20  # max additional boost beyond market_max
MARKET_BOOST_SLOPE = 1.0  # boost per pp of divergence above threshold

# ── V4.7.0: Progressive market boost thresholds ──
# Instead of a hard 15pp cliff, the effective threshold adapts to market
# data quality (bookmaker count + consensus tightness).  Higher-quality
# market data (multi-bookmaker, low CV) gets a lower threshold so the
# boost engages earlier.  Low-quality data (single bookmaker) keeps the
# original 15pp guard.
MARKET_BOOST_THRESHOLD_HIGH_CONSENSUS = 0.10   # ≥6 bookmakers, CV<6%
MARKET_BOOST_THRESHOLD_MEDIUM_CONSENSUS = 0.13  # ≥3 bookmakers
MARKET_BOOST_THRESHOLD_LOW_CONSENSUS = 0.15     # 1-2 bookmakers (original)
DRAW_FLOOR = 0.12  # minimum draw probability for WC group-stage matches
KO_DRAW_FLOOR = 0.18  # minimum draw probability for KO matches (12→18: 4/4 KO draws missed, 2026-07-04 R32 review)

# ── Market consensus gate (P1-1 Phase 2) ──
MARKET_CONSENSUS_GATE_ENABLED = True
MARKET_CONSENSUS_CV_THRESHOLD = 0.03   # CV < 3% = high bookmaker consensus (0.02→0.03: slightly relaxed)
MARKET_CONSENSUS_BOOST = 0.08          # market_max bump when consensus is high (0.05→0.08: stronger boost)
MARKET_CONSENSUS_MAX_CAP = 0.45        # absolute ceiling after consensus boost (0.40→0.45)
MARKET_CONSENSUS_MIN_BOOKMAKERS = 6    # need at least 6 bookmakers for reliable CV


def apply_market_consensus_gate(
    market_max_weight: float,
    market_probs: dict[str, float] | None = None,
) -> tuple[float, dict[str, object]]:
    """Check market consensus CV and adjust market cap when bookmaker agreement is high.

    When max(cv_home, cv_draw, cv_away) < 2% with at least 6 bookmakers,
    the market cap is increased by MARKET_CONSENSUS_BOOST (0.05). This
    addresses the finding that highly-consensus market signals deserve
    more weight (e.g. 12/12 bookmakers agree within tight spread).

    Returns:
        (adjusted_market_max, gate_result) — market_max unchanged if no CV data.
    """
    if not MARKET_CONSENSUS_GATE_ENABLED:
        return market_max_weight, {"checked": False, "reason": "gate disabled"}

    if not market_probs:
        return market_max_weight, {"checked": True, "triggered": False,
                                    "reason": "no market probs available"}

    # ── V4.7.0: Support both CV formats ──
    # API data: {"cv": {"home": X, "draw": Y, "away": Z}}
    # Web consensus: {"cv_home": X, "cv_draw": Y, "cv_away": Z}
    cv = market_probs.get("cv")
    if cv and isinstance(cv, dict) and all(k in cv for k in ("home", "draw", "away")):
        cv_home = float(cv["home"])
        cv_draw = float(cv["draw"])
        cv_away = float(cv["away"])
    elif all(k in market_probs for k in ("cv_home", "cv_draw", "cv_away")):
        cv_home = float(market_probs["cv_home"])
        cv_draw = float(market_probs["cv_draw"])
        cv_away = float(market_probs["cv_away"])
    else:
        return market_max_weight, {"checked": True, "triggered": False,
                                    "reason": "no CV data in market_probs"}

    # V4.7.0: Use the larger bookmaker count (API + web consensus merged)
    api_n = int(market_probs.get("sample_bookmakers", 0))
    web_n = int(market_probs.get("web_sample_bookmakers", 0))
    effective_n = max(api_n, web_n)

    if effective_n < MARKET_CONSENSUS_MIN_BOOKMAKERS:
        return market_max_weight, {
            "checked": True, "triggered": False,
            "reason": f"insufficient bookmakers ({effective_n} < {MARKET_CONSENSUS_MIN_BOOKMAKERS})",
            "sample_bookmakers": effective_n,
        }

    # Use the worst (highest) CV across all three outcomes
    max_cv = max(cv_home, cv_draw, cv_away)

    if max_cv >= MARKET_CONSENSUS_CV_THRESHOLD:
        return market_max_weight, {
            "checked": True, "triggered": False,
            "reason": f"CV ({max_cv:.4f}) >= threshold ({MARKET_CONSENSUS_CV_THRESHOLD})",
            "max_cv": round(max_cv, 6),
            "sample_bookmakers": effective_n,
        }

    # High consensus detected — boost market cap
    adjusted = min(MARKET_CONSENSUS_MAX_CAP, market_max_weight + MARKET_CONSENSUS_BOOST)
    return adjusted, {
        "checked": True,
        "triggered": True,
        "reason": (
            f"High market consensus: CV={max_cv:.2%} < {MARKET_CONSENSUS_CV_THRESHOLD:.0%}, "
            f"{effective_n} bookmakers. market_max {market_max_weight:.2f} → {adjusted:.2f}"
        ),
        "max_cv": round(max_cv, 6),
        "sample_bookmakers": effective_n,
        "original_market_max": market_max_weight,
        "adjusted_market_max": adjusted,
        "boost_applied": round(adjusted - market_max_weight, 4),
    }


# ── Dataclasses ─────────────────────────────────────────────────

@dataclass
class CoreFusionResult:
    """Output of run_core_fusion(): DC → Enhancer → NegBin → Weibull → Elo → Pi."""
    probs: dict[str, float]
    dc_enhancer_divergence_pp: float
    dc_enhancer_direction_conflict: bool
    effective_dc_weight: float
    negbin_applied: bool
    weibull_applied: bool
    negbin_probs: dict[str, float] | None = None


@dataclass
class MarketBoostResult:
    """Output of apply_market_boost(): dynamic market weight adjustment."""
    probs: dict[str, float]
    pre_market_probs: dict[str, float]
    market_applied: bool
    market_weight_used: float
    divergence: float
    boost_attenuated: bool
    threshold_tier: str = "low_consensus"       # V4.7.0: data-quality tier
    effective_threshold: float = 0.15           # V4.7.0: threshold used


# ── Internal helpers ────────────────────────────────────────────

def _normalize_triplet(probs: dict[str, float]) -> dict[str, float]:
    """Normalize H/D/A probabilities to sum to 1.  Falls back to uniform (⅓,⅓,⅓).

    Accepts both ``home_win_prob``/``draw_prob``/``away_win_prob`` and
    ``home``/``draw``/``away`` key conventions.
    """
    h = max(0.0, float(probs.get("home_win_prob", probs.get("home", 1 / 3))))
    d = max(0.0, float(probs.get("draw_prob", probs.get("draw", 1 / 3))))
    a = max(0.0, float(probs.get("away_win_prob", probs.get("away", 1 / 3))))
    total = h + d + a
    if total <= 0:
        return {"home_win_prob": 1 / 3, "draw_prob": 1 / 3, "away_win_prob": 1 / 3}
    return {"home_win_prob": h / total, "draw_prob": d / total, "away_win_prob": a / total}


def _blend_component(
    base: dict[str, float],
    component: dict[str, float],
    weight: float,
) -> dict[str, float]:
    """Weighted blend of a component into the base probs, then renormalize.

    Used for Weibull / Elo / Pi sequential fusion steps.
    ``component`` may use either ``home_win_prob``/… or ``home``/… keys.
    """
    key_map = {
        "home_win_prob": ["home_win_prob", "home"],
        "draw_prob": ["draw_prob", "draw"],
        "away_win_prob": ["away_win_prob", "away"],
    }
    result: dict[str, float] = {}
    for k, aliases in key_map.items():
        c_val = base[k]
        for alias in aliases:
            if alias in component:
                c_val = float(component[alias])
                break
        result[k] = base[k] * (1.0 - weight) + c_val * weight
    return _normalize_triplet(result)


def _favorite(probs: dict[str, float]) -> str:
    """Return the key of the largest probability."""
    normalized = {
        "home": float(probs.get("home_win_prob", probs.get("home", 0.0))),
        "draw": float(probs.get("draw_prob", probs.get("draw", 0.0))),
        "away": float(probs.get("away_win_prob", probs.get("away", 0.0))),
    }
    return max(normalized, key=normalized.get)


# ── NegBin (overdispersion correction) ──────────────────────────

def negbin_pmf(k: int, mu: float, r: float) -> float:
    """Negative Binomial PMF: NB(k; r, p) where p = r/(r+mu)."""
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    p = r / (r + mu)
    log_prob = r * math.log(p)
    for i in range(k):
        log_prob += math.log(r + i) - math.log(i + 1)
    log_prob += k * math.log(1 - p)
    return math.exp(log_prob)


def overdispersed_scoreline(hxg: float, axg: float, max_g: int = 20) -> dict:
    """NegBin H/D/A probabilities with xG calibration applied.

    Returns dict with 'negbin' (calibrated H/D/A), 'poisson' (raw H/D/A),
    and 'matrix' (NB score matrix as nested list, shape (max_g, max_g)).
    """
    hxg_cal = hxg * WC_XG_CALIBRATION_FACTOR
    axg_cal = axg * WC_XG_CALIBRATION_FACTOR

    # Pure Poisson (raw xG, for comparison)
    pp_h = pp_d = pp_a = 0.0
    for h in range(max_g):
        ph = hxg ** h * math.exp(-hxg) / math.factorial(h)
        for a in range(max_g):
            pa = axg ** a * math.exp(-axg) / math.factorial(a)
            p = ph * pa
            if h > a: pp_h += p
            elif h == a: pp_d += p
            else: pp_a += p

    # Calibrated NegBin — build full score matrix alongside H/D/A
    nb_h = nb_d = nb_a = 0.0
    nb_matrix = []
    for h in range(max_g):
        row = []
        ph = negbin_pmf(h, hxg_cal, NEGBIN_R)
        for a in range(max_g):
            pa = negbin_pmf(a, axg_cal, NEGBIN_R)
            p = ph * pa
            row.append(p)
            if h > a: nb_h += p
            elif h == a: nb_d += p
            else: nb_a += p
        nb_matrix.append(row)

    total_nb = nb_h + nb_d + nb_a
    # Normalise matrix (rows and columns are already consistent with H/D/A sums)
    if total_nb > 0:
        for h in range(max_g):
            for a in range(max_g):
                nb_matrix[h][a] /= total_nb

    return {
        "negbin": {"home_win": nb_h / total_nb, "draw": nb_d / total_nb, "away_win": nb_a / total_nb},
        "poisson": {"home_win": pp_h / (pp_h + pp_d + pp_a), "draw": pp_d / (pp_h + pp_d + pp_a), "away_win": pp_a / (pp_h + pp_d + pp_a)},
        "matrix": nb_matrix,
    }


def apply_tau_correction(
    matrix: list[list[float]],
    hxg: float,
    axg: float,
    rho: float,
) -> list[list[float]]:
    """Apply Dixon-Coles τ correction to an existing score matrix.

    The τ correction adjusts four low-score cells (0-0, 1-0, 0-1, 1-1)
    to account for the dependence between home and away goals that
    independent Poisson/NegBin assumptions miss.

    This is the Michels, Ötting & Karlis (2023/2025 JRSS-C) approach:
    applying Dixon-Coles τ to Negative Binomial marginals produces a
    Sarmanov-family bivariate distribution with overdispersion.

    Parameters
    ----------
    matrix:
        2-D list, shape (max_g+1, max_g+1), pre-normalised to sum to 1.
    hxg:
        Home expected goals (before calibration factor).
    axg:
        Away expected goals (before calibration factor).
    rho:
        Dixon-Coles dependence parameter. Typical WC: -0.15 to -0.25.
        Negative rho → more 0-0/1-1, fewer 1-0/0-1.

    Returns
    -------
    list[list[float]]
        τ-corrected matrix, normalised to sum to 1.
    """
    max_g = len(matrix) - 1
    corrected = [row[:] for row in matrix]  # shallow copy

    # Apply τ factors to cells that exist in the matrix
    factors = {
        (0, 0): 1 - (hxg * axg * rho),
        (0, 1): 1 + (hxg * rho),
        (1, 0): 1 + (axg * rho),
        (1, 1): 1 - rho,
    }
    for (h, a), factor in factors.items():
        if h <= max_g and a <= max_g:
            corrected[h][a] = matrix[h][a] * max(factor, 0.05)  # floor to avoid negative

    # Re-normalise
    total = sum(sum(row) for row in corrected)
    if total > 0:
        for h in range(max_g + 1):
            for a in range(max_g + 1):
                corrected[h][a] /= total

    return corrected


def negbin_score_matrix(
    hxg: float,
    axg: float,
    max_g: int = 5,
    r: float | None = None,
    tau_rho: float | None = None,
) -> list[list[float]]:
    """Generate a full score matrix using the Negative Binomial distribution.

    Parameters
    ----------
    hxg:
        Home expected goals (DC model output).
    axg:
        Away expected goals (DC model output).
    max_g:
        Maximum goals per side. Returns (max_g+1)×(max_g+1) matrix.
    r:
        NegBin dispersion parameter.  If None, uses ``NEGBIN_R`` (3.5).
    tau_rho:
        If not None, apply Dixon-Coles τ correction with this ρ value.
        Typical WC ρ ≈ -0.15 to -0.25.  If None, matrix uses raw NB.

    Returns
    -------
    list[list[float]]
        Score probability matrix, shape (max_g+1, max_g+1), sum ≈ 1.0.
    """
    if r is None:
        r = NEGBIN_R

    hxg_cal = hxg * WC_XG_CALIBRATION_FACTOR
    axg_cal = axg * WC_XG_CALIBRATION_FACTOR

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

    # Optional τ correction (Sarmanov-NB model of Michels et al. 2023)
    if tau_rho is not None:
        matrix = apply_tau_correction(matrix, hxg_cal, axg_cal, tau_rho)

    return matrix


def fuse_score_matrices(
    matrices: list[list[list[float]]],
    weights: list[float],
    final_probs: dict[str, float] | None = None,
) -> list[list[float]]:
    """Weighted fusion of multiple score matrices with optional outcome calibration.

    Parameters
    ----------
    matrices:
        List of score matrices, each shape (G+1, G+1), each summing to ≈ 1.
    weights:
        Per-matrix fusion weights (will be normalised to sum to 1).
    final_probs:
        If provided, the fused matrix is calibrated so its H/D/A bucket sums
        match these target probabilities.  Keys: ``home_win_prob``, ``draw_prob``,
        ``away_win_prob``.

    Returns
    -------
    list[list[float]]
        Fused (and optionally calibrated) score matrix.
    """
    if not matrices or not weights or len(matrices) != len(weights):
        raise ValueError("matrices and weights must be non-empty and same length")

    # Normalise weights
    w_total = sum(weights)
    if w_total <= 0:
        raise ValueError("sum of weights must be positive")
    w = [x / w_total for x in weights]

    # Determine output dimensions from first matrix
    G = len(matrices[0]) - 1

    # Weighted average
    fused = [[0.0] * (G + 1) for _ in range(G + 1)]
    for idx, mat in enumerate(matrices):
        w_i = w[idx]
        for h in range(min(len(mat), G + 1)):
            row = mat[h]
            for a in range(min(len(row), G + 1)):
                fused[h][a] += row[a] * w_i

    # Normalise
    total = sum(sum(row) for row in fused)
    if total > 0:
        for h in range(G + 1):
            for a in range(G + 1):
                fused[h][a] /= total

    # Outcome-constrained calibration
    if final_probs is not None:
        fused = _calibrate_matrix_to_outcomes(fused, final_probs)

    return fused


def _calibrate_matrix_to_outcomes(
    matrix: list[list[float]],
    final_probs: dict[str, float],
    eps: float = 1e-12,
) -> list[list[float]]:
    """Rescale a score matrix so H/D/A bucket sums match target probabilities.

    Internal helper used by ``fuse_score_matrices()``.  Same algorithm as
    ``score_matrix_calibrator.py`` but in pure Python (no numpy) so it can
    live in the zero-IO ``engine`` module.
    """
    G = len(matrix) - 1
    p_home_target = float(final_probs.get("home_win_prob", final_probs.get("home", 0.33)))
    p_draw_target = float(final_probs.get("draw_prob", final_probs.get("draw", 0.33)))
    p_away_target = float(final_probs.get("away_win_prob", final_probs.get("away", 0.33)))

    # Bucket sums (before)
    p_home_before = sum(matrix[h][a] for h in range(G + 1) for a in range(G + 1) if h > a)
    p_draw_before = sum(matrix[h][a] for h in range(G + 1) for a in range(G + 1) if h == a)
    p_away_before = sum(matrix[h][a] for h in range(G + 1) for a in range(G + 1) if h < a)

    # Per-bucket scaling
    calibrated = [row[:] for row in matrix]
    for h in range(G + 1):
        for a in range(G + 1):
            if h > a and p_home_before > eps:
                calibrated[h][a] *= p_home_target / p_home_before
            elif h == a and p_draw_before > eps:
                calibrated[h][a] *= p_draw_target / p_draw_before
            elif h < a and p_away_before > eps:
                calibrated[h][a] *= p_away_target / p_away_before

    # Global re-normalisation
    new_total = sum(sum(row) for row in calibrated)
    if new_total > eps:
        for h in range(G + 1):
            for a in range(G + 1):
                calibrated[h][a] /= new_total

    return calibrated

def fuse_dc_enhancer_adaptive(
    dc_probs: dict[str, float],
    enh_probs: dict[str, float],
    dc_base_weight: float,
) -> tuple[dict[str, float], float, bool, float]:
    """Fuse DC and Enhancer with adaptive divergence guard.

    When DC-Enhancer divergence exceeds 20pp, DC weight is reduced by up to
    0.15 (Enhancer is historically unreliable for WC direction).  Direction-
    conflict guard: when DC and Enhancer disagree on the favorite, skip weight
    reduction and use normal fusion.

    Args:
        dc_probs: dict with home_win_prob/draw_prob/away_win_prob
        enh_probs: dict with home_win_prob/draw_prob/away_win_prob
        dc_base_weight: base DC weight from weight config (e.g. 0.68)

    Returns:
        (fused_probs, max_divergence_pp, direction_conflict, effective_dc_weight)
    """
    dc_w_ef = float(dc_base_weight)

    # Compute per-outcome divergence
    divs = {}
    for key in ("home_win_prob", "draw_prob", "away_win_prob"):
        divs[key] = abs(dc_probs[key] - enh_probs[key]) * 100
    max_div = float(max(divs.values()))

    dc_fav = max(dc_probs, key=dc_probs.get)
    enh_fav = max(enh_probs, key=enh_probs.get)
    direction_conflict = (dc_fav != enh_fav)

    if max_div > 20 and not direction_conflict:
        shift = min(0.15, (max_div - 20) * 0.015)
        dc_w_ef = max(0.30, dc_base_weight - shift)

    # Always use manual weighted-fusion (was: lazy-imported fuse_outcome_probabilities
    # for normal case; now inlined to avoid circular dependency on tabular_match_model)
    enh_w = 1.0 - dc_w_ef
    fused = _normalize_triplet({
        k: dc_probs[k] * dc_w_ef + enh_probs[k] * enh_w
        for k in ("home_win_prob", "draw_prob", "away_win_prob")
    })

    return fused, max_div, direction_conflict, dc_w_ef


def enforce_draw_floor(
    probs: dict[str, float],
    floor: float = DRAW_FLOOR,
) -> tuple[dict[str, float], bool]:
    """Enforce a minimum draw probability floor.

    Deficit allocated 70% from favorite, 30% from underdog.
    Returns (adjusted_probs, was_applied).
    """
    if probs.get("draw_prob", 0) >= floor:
        return dict(probs), False

    deficit = floor - probs["draw_prob"]
    if probs.get("home_win_prob", 0) >= probs.get("away_win_prob", 0):
        probs["home_win_prob"] = max(0.02, probs["home_win_prob"] - deficit * 0.7)
        probs["away_win_prob"] = max(0.02, probs["away_win_prob"] - deficit * 0.3)
    else:
        probs["home_win_prob"] = max(0.02, probs["home_win_prob"] - deficit * 0.3)
        probs["away_win_prob"] = max(0.02, probs["away_win_prob"] - deficit * 0.7)
    probs["draw_prob"] = floor
    return _normalize_triplet(probs), True


def attenuate_market_boost(
    boost: float,
    *,
    dc_enhancer_divergence_pp: float,
    dc_enhancer_direction_conflict: bool,
    pre_market_probs: dict[str, float],
    market_probs: dict[str, float],
    divergence_threshold_pp: float = MARKET_BOOST_DC_ENH_DIVERGENCE_PP,
    attenuation: float = MARKET_BOOST_ATTENUATION,
) -> tuple[float, bool]:
    """Reduce a dynamic market boost when model consensus is unreliable.

    Attenuation is applied only when all of these conditions hold:
    - DC and Enhancer differ by more than the configured threshold;
    - DC and Enhancer select different most-likely outcomes; and
    - the fused pre-market model and the market select different outcomes.

    The helper accepts either ``home_win_prob``/``draw_prob``/
    ``away_win_prob`` or ``home``/``draw``/``away`` keys.
    """
    if boost <= 0:
        return float(boost), False
    if not 0.0 <= attenuation <= 1.0:
        raise ValueError("attenuation must be between 0 and 1")

    should_attenuate = (
        float(dc_enhancer_divergence_pp) > float(divergence_threshold_pp)
        and bool(dc_enhancer_direction_conflict)
        and _favorite(pre_market_probs) != _favorite(market_probs)
    )
    if not should_attenuate:
        return float(boost), False
    return float(boost) * float(attenuation), True


# ── Unified fusion chain ────────────────────────────────────────

def run_core_fusion(
    *,
    dc_probs: dict[str, float],
    dc_home_xg: float,
    dc_away_xg: float,
    dc_base_weight: float,
    enh_probs: dict[str, float] | None = None,
    weibull_probs: dict[str, float] | None = None,
    weibull_weight: float = 0.0,
    elo_probs: dict[str, float] | None = None,
    elo_weight: float = 0.0,
    pi_probs: dict[str, float] | None = None,
    pi_weight: float = 0.0,
) -> CoreFusionResult:
    """Run the core fusion chain: DC → Enhancer → NegBin → Weibull → Elo → Pi.

    Pure math — no I/O, no side effects.  All component probabilities are
    passed in explicitly.  Callers are responsible for loading models and
    generating component predictions.

    The fusion is *sequential* (not a flat weighted average): each step
    blends its component into the running fused state at its configured
    weight, so early components are diluted by later steps' ``(1 - w)``
    multipliers.

    Returns a ``CoreFusionResult`` with the fused probabilities and
    metadata needed by downstream steps (market boost, draw floor,
    calibration, and learning-engine attribution).
    """
    # ── Step 1: DC baseline ──
    fused = {
        "home_win_prob": float(dc_probs["home_win_prob"]),
        "draw_prob": float(dc_probs["draw_prob"]),
        "away_win_prob": float(dc_probs["away_win_prob"]),
    }

    divergence_pp = 0.0
    direction_conflict = False
    dc_w_ef = dc_base_weight

    # ── Step 2: DC + Enhancer (adaptive) ──
    if enh_probs is not None:
        fused, divergence_pp, direction_conflict, dc_w_ef = \
            fuse_dc_enhancer_adaptive(fused, enh_probs, dc_base_weight)

    # ── Step 3: NegBin 5% (overdispersion correction) ──
    negbin_applied = False
    negbin_probs_result: dict[str, float] | None = None
    if dc_home_xg > 0 and dc_away_xg > 0:
        try:
            od_sl = overdispersed_scoreline(dc_home_xg, dc_away_xg)
            nb_probs = od_sl["negbin"]
            for k in ("home_win_prob", "draw_prob", "away_win_prob"):
                nb_key = {"home_win_prob": "home_win", "draw_prob": "draw", "away_win_prob": "away_win"}[k]
                fused[k] = fused[k] * (1 - NEGBIN_FUSION_WEIGHT) + nb_probs[nb_key] * NEGBIN_FUSION_WEIGHT
            negbin_probs_result = {
                "home": float(nb_probs["home_win"]),
                "draw": float(nb_probs["draw"]),
                "away": float(nb_probs["away_win"]),
            }
            negbin_applied = True
        except Exception:
            pass  # NegBin is best-effort; failure is non-fatal

    # ── Step 4: Weibull ──
    weibull_applied = False
    if weibull_probs is not None and weibull_weight > 0:
        fused = _blend_component(fused, weibull_probs, weibull_weight)
        weibull_applied = True

    # ── Step 5: Elo ──
    if elo_probs is not None and elo_weight > 0:
        fused = _blend_component(fused, elo_probs, elo_weight)

    # ── Step 6: Pi-Rating ──
    if pi_probs is not None and pi_weight > 0:
        fused = _blend_component(fused, pi_probs, pi_weight)

    return CoreFusionResult(
        probs=fused,
        dc_enhancer_divergence_pp=divergence_pp,
        dc_enhancer_direction_conflict=direction_conflict,
        effective_dc_weight=dc_w_ef,
        negbin_applied=negbin_applied,
        weibull_applied=weibull_applied,
        negbin_probs=negbin_probs_result,
    )


def apply_market_boost(
    *,
    fused: dict[str, float],
    market_probs: dict[str, float],
    market_max_weight: float,
    dc_enhancer_divergence_pp: float,
    dc_enhancer_direction_conflict: bool,
    pre_market_probs: dict[str, float] | None = None,
) -> MarketBoostResult:
    """Apply dynamic market boost when model-market divergence exceeds threshold.

    Unified implementation used by the canonical prediction pipeline and
    compatibility callers.

    When the model's fused probabilities diverge from market-implied
    probabilities by more than ``MARKET_BOOST_DIVERGENCE_THRESHOLD`` (15pp),
    the market weight is temporarily boosted above ``market_max_weight``.
    The boost is attenuated (×0.6) when DC-Enhancer consensus is unreliable
    (direction conflict + both diverge from market).

    Args:
        fused: Current fused probabilities (after all model components).
        market_probs: Market-implied probabilities (keys: home_prob/draw_prob/away_prob).
        market_max_weight: Base market weight from weight config (e.g. 0.30).
        dc_enhancer_divergence_pp: DC-Enhancer max divergence in percentage points.
        dc_enhancer_direction_conflict: Whether DC and Enhancer disagree on favorite.
        pre_market_probs: Snapshot of fused probs before market (defaults to ``fused``).

    Returns:
        MarketBoostResult with updated probs and metadata.
    """
    snapshot = dict(pre_market_probs) if pre_market_probs is not None else dict(fused)

    model_market_div = max(
        abs(fused.get("home_win_prob", fused.get("home", 0.33)) - market_probs.get("home_prob", 0.5)),
        abs(fused.get("draw_prob", fused.get("draw", 0.33)) - market_probs.get("draw_prob", 0.25)),
        abs(fused.get("away_win_prob", fused.get("away", 0.33)) - market_probs.get("away_prob", 0.25)),
    )

    # ── V4.7.0: Data-quality-aware progressive threshold ──
    # Instead of a hard 15pp cliff, the effective threshold adapts to how
    # reliable the market data is.  Multi-bookmaker consensus with low CV
    # (tight agreement) gets a lower threshold — the boost engages earlier.
    bookmaker_count = int(market_probs.get("sample_bookmakers", 1))
    cv_home = float(market_probs.get("cv_home",
                    market_probs.get("cv", {}).get("home", 0.10)))
    cv_draw = float(market_probs.get("cv_draw",
                    market_probs.get("cv", {}).get("draw", 0.10)))
    cv_away = float(market_probs.get("cv_away",
                    market_probs.get("cv", {}).get("away", 0.10)))
    cv_max = max(cv_home, cv_draw, cv_away)

    if bookmaker_count >= 6 and cv_max < 0.06:
        effective_threshold = MARKET_BOOST_THRESHOLD_HIGH_CONSENSUS   # 0.10
        threshold_tier = "high_consensus"
    elif bookmaker_count >= 3:
        effective_threshold = MARKET_BOOST_THRESHOLD_MEDIUM_CONSENSUS  # 0.13
        threshold_tier = "medium_consensus"
    else:
        effective_threshold = MARKET_BOOST_THRESHOLD_LOW_CONSENSUS    # 0.15
        threshold_tier = "low_consensus"

    if model_market_div <= effective_threshold:
        return MarketBoostResult(
            probs=dict(fused),
            pre_market_probs=snapshot,
            market_applied=False,
            market_weight_used=market_max_weight,
            divergence=model_market_div,
            boost_attenuated=False,
            threshold_tier=threshold_tier,
            effective_threshold=effective_threshold,
        )

    # Compute boost (slope anchored at effective_threshold, not hard-coded 0.15)
    boost = min(MARKET_BOOST_MAX,
                (model_market_div - effective_threshold) * MARKET_BOOST_SLOPE)
    boost, boost_attenuated = attenuate_market_boost(
        boost,
        dc_enhancer_divergence_pp=dc_enhancer_divergence_pp,
        dc_enhancer_direction_conflict=dc_enhancer_direction_conflict,
        pre_market_probs=snapshot,
        market_probs=market_probs,
    )
    boosted_weight = min(0.50, market_max_weight + boost)

    # Blend
    result = dict(fused)
    result["home_win_prob"] = fused.get("home_win_prob", fused.get("home", 0.33)) * (1 - boosted_weight) \
        + market_probs["home_prob"] * boosted_weight
    result["draw_prob"] = fused.get("draw_prob", fused.get("draw", 0.33)) * (1 - boosted_weight) \
        + market_probs["draw_prob"] * boosted_weight
    result["away_win_prob"] = fused.get("away_win_prob", fused.get("away", 0.33)) * (1 - boosted_weight) \
        + market_probs["away_prob"] * boosted_weight

    result = _normalize_triplet(result)

    return MarketBoostResult(
        probs=result,
        pre_market_probs=snapshot,
        market_applied=True,
        market_weight_used=boosted_weight,
        divergence=model_market_div,
        boost_attenuated=boost_attenuated,
        threshold_tier=threshold_tier,
        effective_threshold=effective_threshold,
    )
