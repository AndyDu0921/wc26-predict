"""Probability utilities for market odds — vig removal, normalization.

All functions are pure (no side effects, no network I/O).
"""
from __future__ import annotations

import math


def normalize_1x2_odds(
    home_odds: float,
    draw_odds: float,
    away_odds: float,
) -> dict[str, float]:
    """Basic proportional vig removal for 1X2 (home/draw/away) decimal odds.

    Converts decimal odds → implied probabilities → normalizes to sum=1.0.

    Args:
        home_odds: Decimal odds for home win (e.g., 2.10)
        draw_odds: Decimal odds for draw (e.g., 3.50)
        away_odds: Decimal odds for away win (e.g., 3.80)

    Returns:
        dict with home, draw, away probabilities (sum ≈ 1.0) and overround.
    """
    if any(o <= 1.0 for o in (home_odds, draw_odds, away_odds)):
        raise ValueError(f"Odds must be > 1.0, got {home_odds}/{draw_odds}/{away_odds}")

    raw_home = 1.0 / home_odds
    raw_draw = 1.0 / draw_odds
    raw_away = 1.0 / away_odds
    total = raw_home + raw_draw + raw_away

    return {
        "home": raw_home / total,
        "draw": raw_draw / total,
        "away": raw_away / total,
        "overround": total - 1.0,
    }


def normalize_1x2_shin(
    home_odds: float,
    draw_odds: float,
    away_odds: float,
    max_iter: int = 100,
    tolerance: float = 1e-8,
) -> dict[str, float]:
    """Shin (1993) method for vig removal — handles favorite-longshot bias.

    Iteratively solves for the proportion of informed bettors (z) and
    true probabilities. More accurate than proportional method when
    favorite-longshot bias is present.

    Args:
        home_odds: Decimal odds for home win
        draw_odds: Decimal odds for draw
        away_odds: Decimal odds for away win
        max_iter: Maximum iterations for root finding
        tolerance: Convergence tolerance

    Returns:
        dict with home, draw, away probabilities, overround, and z (informed fraction).
    """
    if any(o <= 1.0 for o in (home_odds, draw_odds, away_odds)):
        raise ValueError("Odds must be > 1.0")

    # A non-convergent Shin solve falls back to proportional vig removal from
    # the same observed odds, never to invented uniform probabilities.
    proportional = normalize_1x2_odds(home_odds, draw_odds, away_odds)
    z = 0.0
    probs = {
        "home": proportional["home"],
        "draw": proportional["draw"],
        "away": proportional["away"],
    }

    for _ in range(max_iter):
        z_prev = z
        # E[informed prob] under current z
        p_home = _shin_implied(home_odds, z)
        p_draw = _shin_implied(draw_odds, z)
        p_away = _shin_implied(away_odds, z)
        total = p_home + p_draw + p_away

        if abs(total - 1.0) < tolerance:
            probs = {"home": p_home, "draw": p_draw, "away": p_away}
            break

        # Adjust z: if total > 1, increase z
        z = z + 0.5 * (total - 1.0)
        z = max(0.0, min(z, 0.5))  # z ∈ [0, 0.5]

        if abs(z - z_prev) < tolerance:
            probs = {"home": p_home / total, "draw": p_draw / total, "away": p_away / total}
            break

    return {
        "home": probs["home"],
        "draw": probs["draw"],
        "away": probs["away"],
        "overround": sum(
            1.0 / o for o in (home_odds, draw_odds, away_odds)
        ) - 1.0,
        "z": z,
    }


def _shin_implied(odds: float, z: float) -> float:
    """Shin's implied probability for a single outcome.

    Shin (1993) closed-form solution:
      p_i = (sqrt(z^2 + 4*(1-z)*(1/odds_i)) - z) / (2*(1-z))

    When z = 0 (no informed bettors), this reduces to the proportional
    method: p_i = 1/odds_i.  The previous linear approximation (1-z)/odds
    was incorrect for all z > 0.
    """
    if z < 1e-12:
        return 1.0 / odds
    inv = 1.0 / odds
    return (math.sqrt(z * z + 4.0 * (1.0 - z) * inv) - z) / (2.0 * (1.0 - z))


def normalize_1x2_power(
    home_odds: float,
    draw_odds: float,
    away_odds: float,
    power: float = 0.5,
) -> dict[str, float]:
    """Power method for vig removal.

    Raises implied probabilities to a power < 1 to account for
    favorite-longshot bias. Lower power = more correction.

    Args:
        power: Correction exponent, typically 0.4-0.6. Lower = more aggressive.

    Returns:
        dict with home, draw, away probabilities and overround.
    """
    raw_home = (1.0 / home_odds) ** power
    raw_draw = (1.0 / draw_odds) ** power
    raw_away = (1.0 / away_odds) ** power
    total = raw_home + raw_draw + raw_away

    return {
        "home": raw_home / total,
        "draw": raw_draw / total,
        "away": raw_away / total,
        "overround": (1.0 / home_odds + 1.0 / draw_odds + 1.0 / away_odds) - 1.0,
    }


def correct_domain_bias(
    home: float,
    draw: float,
    away: float,
) -> dict[str, float]:
    """Apply domain-driven bias correction (Karimov et al., 2025).

    Bookmakers systematically overestimate draw and away-win probabilities.
    Karimov et al. analyzed 359,035 matches and found that even after vig
    removal, residual biases remain:

      - Draw: overestimated by 5-8% (stronger in balanced matches)
      - Away win: overestimated by 3-5% (stronger when away is underdog)
      - Home win: underestimated for strong favorites

    This function applies empirically-calibrated correction factors then
    renormalizes to sum = 1.0.

    Args:
        home: De-vigged home win probability (0..1).
        draw: De-vigged draw probability (0..1).
        away: De-vigged away win probability (0..1).

    Returns:
        dict with corrected home, draw, away (sum ≈ 1.0).
    """
    # ── Domain detection ──────────────────────────────────────────
    if home > 0.50:
        # Domain A: Strong home favorite
        # Draw overestimated ~6%, away (underdog) overestimated ~4%
        draw_corr = 1.0 - 0.06
        away_corr = 1.0 - 0.04
        home_corr = 1.0
    elif abs(home - away) < 0.10:
        # Domain B: Balanced match
        # Draw overestimated ~5%, both sides similarly treated
        draw_corr = 1.0 - 0.05
        away_corr = 1.0 - 0.02
        home_corr = 1.0 - 0.02
    elif away > home + 0.05:
        # Domain C: Away favorite
        # Draw overestimated ~5%, home (underdog) overestimated ~3%
        draw_corr = 1.0 - 0.05
        home_corr = 1.0 - 0.03
        away_corr = 1.0
    else:
        # Domain D: Slight home edge (0.40 < home <= 0.50, |gap| < 0.15)
        draw_corr = 1.0 - 0.05
        away_corr = 1.0 - 0.03
        home_corr = 1.0

    # ── Apply corrections ─────────────────────────────────────────
    h = home * home_corr
    d = draw * draw_corr
    a = away * away_corr
    total = h + d + a

    return {
        "home": h / total,
        "draw": d / total,
        "away": a / total,
    }


def normalize_1x2_domain_driven(
    home_odds: float,
    draw_odds: float,
    away_odds: float,
) -> dict[str, float]:
    """Proportional de-vig + Karimov et al. (2025) domain-driven correction.

    Combines the basic proportional vig removal with systematic bookmaker
    bias correction based on analysis of 359,035 matches. More accurate
    than pure proportional de-vig, especially for draw and away-win
    probabilities.

    Args:
        home_odds: Decimal odds for home win.
        draw_odds: Decimal odds for draw.
        away_odds: Decimal odds for away win.

    Returns:
        dict with home, draw, away probabilities (sum ≈ 1.0) and overround.
    """
    base = normalize_1x2_odds(home_odds, draw_odds, away_odds)
    corrected = correct_domain_bias(base["home"], base["draw"], base["away"])
    corrected["overround"] = base["overround"]
    return corrected
