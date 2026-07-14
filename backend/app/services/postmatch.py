"""postmatch.py — Post-match prediction evaluation engine.

Evaluates a single prediction against the actual match result using:
- Brier Score (probability calibration, 0=perfect, 1=worst)
- Log Loss (logarithmic scoring rule, lower=better)
- Ranked Probability Score (RPS, for ordinal 3-outcome)
- Directional accuracy (did the highest-prob outcome happen?)
- Score hit (was the exact score in top-N predicted?)

Also generates a structured MatchReview dataclass for Dashboard display.
"""

from __future__ import annotations
import logging

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class MatchReview:
    """Complete post-match review of a single prediction."""

    # ── Match info ──
    home_team: str
    away_team: str
    competition: str
    match_date: str = ""

    # ── Actual result ──
    actual_home_goals: int = 0
    actual_away_goals: int = 0
    actual_outcome: str = ""  # "home" | "draw" | "away"

    # ── Predicted probabilities ──
    pred_home_prob: float = 0.333
    pred_draw_prob: float = 0.334
    pred_away_prob: float = 0.333
    pred_home_xg: float = 0.0
    pred_away_xg: float = 0.0
    pred_top_scores: list[dict[str, Any]] = field(default_factory=list)

    # ── Evaluation metrics ──
    brier_score: float = 0.0
    log_loss: float = 0.0
    rps: float = 0.0
    directional_correct: bool = False
    exact_score_hit: bool = False
    top3_score_hit: bool = False
    xg_error: float = 0.0

    # ── Qualitative grade ──
    grade: str = ""  # "A+" to "F"
    grade_reason: str = ""

    # ── LLM review ──
    ai_review: str | None = None

    @property
    def actual_score_str(self) -> str:
        return f"{self.actual_home_goals}:{self.actual_away_goals}"

    @property
    def predicted_favorite(self) -> str:
        if self.pred_home_prob > self.pred_draw_prob and self.pred_home_prob > self.pred_away_prob:
            return self.home_team
        elif self.pred_away_prob > self.pred_home_prob and self.pred_away_prob > self.pred_draw_prob:
            return self.away_team
        return "draw"

    def to_dict(self) -> dict[str, Any]:
        return {
            "home_team": self.home_team,
            "away_team": self.away_team,
            "competition": self.competition,
            "actual_score": self.actual_score_str,
            "actual_outcome": self.actual_outcome,
            "pred_home_prob": self.pred_home_prob,
            "pred_draw_prob": self.pred_draw_prob,
            "pred_away_prob": self.pred_away_prob,
            "brier_score": round(self.brier_score, 4),
            "log_loss": round(self.log_loss, 4),
            "rps": round(self.rps, 4),
            "directional_correct": self.directional_correct,
            "exact_score_hit": self.exact_score_hit,
            "top3_score_hit": self.top3_score_hit,
            "grade": self.grade,
            "grade_reason": self.grade_reason,
            "ai_review": self.ai_review,
        }


# ── Core evaluation functions ─────────────────────────────────────────────────


def evaluate_prediction(
    pred_result: dict[str, Any],
    actual_home_goals: int,
    actual_away_goals: int,
) -> MatchReview:
    """Evaluate a single prediction against actual match result.

    Args:
        pred_result: Result dict from the canonical prediction pipeline.
        actual_home_goals: Actual home team goals.
        actual_away_goals: Actual away team goals.

    Returns:
        MatchReview with all metrics populated.
    """
    review = MatchReview(
        home_team=pred_result.get("home_team", ""),
        away_team=pred_result.get("away_team", ""),
        competition=pred_result.get("competition", ""),
        actual_home_goals=actual_home_goals,
        actual_away_goals=actual_away_goals,
        pred_home_prob=pred_result.get("home_win_prob", 0.333),
        pred_draw_prob=pred_result.get("draw_prob", 0.334),
        pred_away_prob=pred_result.get("away_win_prob", 0.333),
        pred_home_xg=pred_result.get("home_xg", 0),
        pred_away_xg=pred_result.get("away_xg", 0),
        pred_top_scores=pred_result.get("top_scores", []),
    )

    # Determine actual outcome
    if actual_home_goals > actual_away_goals:
        review.actual_outcome = "home"
    elif actual_home_goals == actual_away_goals:
        review.actual_outcome = "draw"
    else:
        review.actual_outcome = "away"

    # ── Brier Score ──
    probs = np.array([
        review.pred_home_prob,
        review.pred_draw_prob,
        review.pred_away_prob,
    ])
    actual = np.zeros(3)
    if review.actual_outcome == "home":
        actual[0] = 1.0
    elif review.actual_outcome == "draw":
        actual[1] = 1.0
    else:
        actual[2] = 1.0
    review.brier_score = float(((probs - actual) ** 2).sum())

    # ── Log Loss ──
    outcome_idx = int(np.argmax(actual))
    review.log_loss = float(-math.log(max(probs[outcome_idx], 1e-12)))

    # ── Ranked Probability Score ──
    # RPS for 3-category ordinal: (home > draw > away)
    cum_pred = np.cumsum(probs)
    cum_actual = np.cumsum(actual)
    review.rps = float(((cum_pred - cum_actual) ** 2).sum() / 2)

    # ── Directional accuracy ──
    favorite = review.predicted_favorite
    if favorite == "draw":
        review.directional_correct = (review.actual_outcome == "draw")
    else:
        review.directional_correct = (
            (favorite == review.home_team and review.actual_outcome == "home")
            or (favorite == review.away_team and review.actual_outcome == "away")
        )

    # ── Score hits ──
    actual_score = review.actual_score_str
    for i, s in enumerate(review.pred_top_scores):
        if s.get("score") == actual_score:
            if i == 0:
                review.exact_score_hit = True
            review.top3_score_hit = True
            break

    # ── xG error ──
    review.xg_error = abs(
        (review.pred_home_xg - review.pred_away_xg)
        - (actual_home_goals - actual_away_goals)
    )

    # ── Qualitative grade ──
    review.grade, review.grade_reason = _assign_grade(review)

    return review


def _assign_grade(review: MatchReview) -> tuple[str, str]:
    """Assign a qualitative grade based on prediction quality.

    Brier score range: [0, 2] for 3-outcome predictions (no per-class division).
    Uniform prediction (0.333, 0.333, 0.333) → Brier ≈ 0.667 (baseline).

    Grades:
        A+: Exact score hit with strong probability
        A:  Direction correct + score in top-3
        B+: Direction correct
        B:  Direction wrong but top-3 score hit
        C:  Direction wrong but better than uniform (Brier < 0.67)
        D:  Clear miss (0.67 ≤ Brier < 1.05)
        F:  Complete miss with high confidence on wrong outcome (Brier ≥ 1.05)
    """
    if review.exact_score_hit:
        return ("A+", "精确命中比分，模型表现优异")
    if review.directional_correct and review.top3_score_hit:
        return ("A", "方向正确且比分进入Top-3预测")
    if review.directional_correct:
        return ("B+", "方向正确但比分未命中，模型方向判断准确")
    if review.top3_score_hit:
        return ("B", "方向判断错误但比分进入Top-3，概率校准仍有参考价值")
    if review.brier_score < 0.67:
        return ("C", f"方向错误，但优于随机猜测 (Brier={review.brier_score:.3f})")
    if review.brier_score < 1.05:
        return ("D", f"明显偏差 (Brier={review.brier_score:.3f})，模型对实际结果置信度不足")
    return ("F", f"严重偏差 (Brier={review.brier_score:.3f})，模型以较高置信度预测了错误结果")


# ── Comparison summary ────────────────────────────────────────────────────────


def generate_comparison_text(review: MatchReview) -> str:
    """Generate a human-readable comparison between prediction and reality."""
    lines = [
        f"## {review.home_team} vs {review.away_team} — 赛后复盘",
        "",
        f"**赛事**: {review.competition}",
        f"**实际比分**: {review.actual_score_str} ({review.actual_outcome})",
        "",
        "### 预测 vs 实际",
        "",
        f"| 指标 | 预测 | 实际 |",
        f"|---|---|---|",
        f"| {review.home_team} 胜 | {review.pred_home_prob*100:.1f}% | {'✅' if review.actual_outcome == 'home' else '—'} |",
        f"| 平局 | {review.pred_draw_prob*100:.1f}% | {'✅' if review.actual_outcome == 'draw' else '—'} |",
        f"| {review.away_team} 胜 | {review.pred_away_prob*100:.1f}% | {'✅' if review.actual_outcome == 'away' else '—'} |",
        f"| xG 差 | {review.pred_home_xg - review.pred_away_xg:+.2f} | {review.actual_home_goals - review.actual_away_goals:+d} |",
        "",
        "### 评估指标",
        "",
        f"| 指标 | 值 | 说明 |",
        f"|---|---|---|",
        f"| Brier Score | {review.brier_score:.4f} | 0=完美, 1=最差 |",
        f"| Log Loss | {review.log_loss:.4f} | 越低越好 |",
        f"| RPS | {review.rps:.4f} | 越低越好 |",
        f"| 方向正确 | {'✅' if review.directional_correct else '❌'} | 概率最高项是否发生 |",
        f"| 比分命中 | {'✅' if review.exact_score_hit else '❌'} | 最可能比分是否命中 |",
        f"| 综合评级 | **{review.grade}** | {review.grade_reason} |",
    ]
    return "\n".join(lines)
