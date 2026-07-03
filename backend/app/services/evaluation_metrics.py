"""Proper scoring metrics for three-way football predictions."""

from __future__ import annotations

import math
from dataclasses import dataclass


OUTCOME_KEYS = ("home", "draw", "away")


@dataclass(frozen=True)
class ThreeWayMetrics:
    """Metric bundle for one 1X2 probability prediction."""

    brier: float
    log_loss: float
    rps: float
    correct: bool


def outcome_index(home_goals: int, away_goals: int) -> int:
    """Return 0=home win, 1=draw, 2=away win."""
    if home_goals > away_goals:
        return 0
    if home_goals == away_goals:
        return 1
    return 2


def normalize_probs(home: float, draw: float, away: float) -> tuple[float, float, float]:
    """Clip and normalize probabilities without silently accepting bad sums."""
    values = [max(float(home), 0.0), max(float(draw), 0.0), max(float(away), 0.0)]
    total = sum(values)
    if total <= 0:
        return (1 / 3, 1 / 3, 1 / 3)
    return (values[0] / total, values[1] / total, values[2] / total)


def brier_score(probs: tuple[float, float, float], actual_index: int) -> float:
    """Multiclass Brier score using the unscaled sum convention."""
    return sum((prob - (1.0 if idx == actual_index else 0.0)) ** 2 for idx, prob in enumerate(probs))


def log_loss(probs: tuple[float, float, float], actual_index: int, eps: float = 1e-12) -> float:
    """Negative log probability assigned to the realised result."""
    return -math.log(max(min(probs[actual_index], 1.0 - eps), eps))


def ranked_probability_score(probs: tuple[float, float, float], actual_index: int) -> float:
    """RPS for the ordered 1X2 vector [home, draw, away]."""
    score = 0.0
    pred_cum = 0.0
    actual_cum = 0.0
    for idx, prob in enumerate(probs):
        pred_cum += prob
        actual_cum += 1.0 if idx == actual_index else 0.0
        score += (pred_cum - actual_cum) ** 2
    return score / 2.0


def evaluate_three_way(
    *,
    home_prob: float,
    draw_prob: float,
    away_prob: float,
    home_goals: int,
    away_goals: int,
) -> ThreeWayMetrics:
    """Evaluate a single three-way prediction against a final score."""
    probs = normalize_probs(home_prob, draw_prob, away_prob)
    actual = outcome_index(home_goals, away_goals)
    predicted = max(range(3), key=lambda idx: probs[idx])
    return ThreeWayMetrics(
        brier=brier_score(probs, actual),
        log_loss=log_loss(probs, actual),
        rps=ranked_probability_score(probs, actual),
        correct=predicted == actual,
    )


# ═══════════════════════════════════════════════════════════════════════
#  Score-level evaluation metrics (V4.7-score)
#  Wheatcroft (2021, JQAS): Log Score is the recommended proper scoring
#  rule for football scoreline prediction — local, intuitive, and faster
#  at discriminating forecast quality than RPS or Brier.
# ═══════════════════════════════════════════════════════════════════════


def score_matrix_log_loss(
    matrix: list[list[float]],
    home_goals: int,
    away_goals: int,
    eps: float = 1e-12,
) -> float:
    """Log Score on a score probability matrix.

    Also known as Ignorance Score or Negative Log-Likelihood.  Recommended
    by Wheatcroft (2021) as the primary proper scoring rule for football
    scoreline forecasts — it is local (only uses the probability assigned
    to the actual outcome) and discriminates forecast quality faster than
    the Brier score.

    Parameters
    ----------
    matrix:
        2-D list where ``matrix[h][a]`` = P(home goals=h, away goals=a).
    home_goals:
        Actual home goals scored.
    away_goals:
        Actual away goals scored.

    Returns
    -------
    float
        ``-ln(P(actual_score))``.  Lower is better.  Perfect = 0.
    """
    if home_goals < len(matrix) and away_goals < len(matrix[0]):
        p = matrix[home_goals][away_goals]
    else:
        p = 0.0
    p = max(min(float(p), 1.0 - eps), eps)
    return -math.log(p)


def score_matrix_brier(
    matrix: list[list[float]],
    home_goals: int,
    away_goals: int,
) -> float:
    """Brier Score on a score probability matrix.

    Treats the full (G+1)×(G+1) matrix as a multi-class prediction where
    only one cell (the actual score) is 1 and all others are 0.

    ``BS = Σ_{i,j} (p_{ij} - o_{ij})²``
    where ``o_{ij} = 1`` at the actual score and 0 elsewhere.

    Non-local: rewards probability placed on nearby scorelines even when
    they did not occur.  Use Log Score as the primary metric.
    """
    G = len(matrix) - 1
    total = 0.0
    for h in range(G + 1):
        for a in range(G + 1):
            target = 1.0 if (h == home_goals and a == away_goals) else 0.0
            diff = matrix[h][a] - target
            total += diff * diff
    return total


def score_matrix_top_n_hit(
    matrix: list[list[float]],
    home_goals: int,
    away_goals: int,
    n: int = 3,
) -> bool:
    """Check if the actual score is among the top-N most probable scorelines.

    Parameters
    ----------
    matrix:
        2-D score probability matrix.
    home_goals, away_goals:
        Actual score.
    n:
        How many top predictions to check (default 3).

    Returns
    -------
    bool
        True if the actual score is in the top-N predictions.
    """
    G = len(matrix) - 1
    flat = []
    for h in range(G + 1):
        for a in range(G + 1):
            flat.append((matrix[h][a], h, a))
    top_n = set()
    for _, h, a in sorted(flat, reverse=True)[:n]:
        top_n.add((h, a))
    return (home_goals, away_goals) in top_n


def score_matrix_exact_hit(
    matrix: list[list[float]],
    home_goals: int,
    away_goals: int,
) -> bool:
    """Check if the most probable scoreline matches the actual score."""
    return score_matrix_top_n_hit(matrix, home_goals, away_goals, n=1)
