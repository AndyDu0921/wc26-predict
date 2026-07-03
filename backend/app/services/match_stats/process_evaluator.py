"""Process evaluator: compares pre-match predicted xG with post-match actual xG.

Core insight: Brier/LogLoss tells us IF we were wrong.
Process evaluation tells us WHY — was the model logic sound but the result
random, or was the model fundamentally wrong about team strengths?
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.services.match_stats.normalizer import safe_float as _safe_float
from app.services.match_stats.normalizer import safe_int as _safe_int


@dataclass
class ProcessEvalResult:
    """Output of process evaluation for one match."""
    match_id: int

    # Predicted xG (from pre_match_snapshots or DC model output)
    predicted_home_xg: Optional[float] = None
    predicted_away_xg: Optional[float] = None

    # Actual stats (from match_team_statistics)
    actual_home_xg: Optional[float] = None
    actual_away_xg: Optional[float] = None
    actual_home_goals: Optional[int] = None
    actual_away_goals: Optional[int] = None

    # xG error metrics
    xg_home_error: Optional[float] = None
    xg_away_error: Optional[float] = None
    xg_mae: Optional[float] = None  # Mean Absolute Error across both teams
    xg_direction_correct: Optional[int] = None  # 1 if predicted xG winner == actual xG winner

    # Total goals
    predicted_total_goals: Optional[float] = None
    actual_total_xg: Optional[float] = None
    total_xg_error: Optional[float] = None

    # Finishing deltas (goals_scored - xg)
    finishing_delta_home: Optional[float] = None
    finishing_delta_away: Optional[float] = None

    # Shot volume deltas
    shot_volume_delta_home: Optional[float] = None
    shot_volume_delta_away: Optional[float] = None

    # Dominance index
    dominance_index_home: Optional[float] = None
    dominance_index_away: Optional[float] = None

    # Classification
    process_winner: Optional[str] = None  # 'home', 'away', 'draw'
    outcome_correct: bool = False
    process_correct: bool = False
    xg_result_alignment: str = "unclear"  # 'aligned', 'contradicted', 'unclear'
    process_label: str = "PROCESS_UNCLEAR"  # 'PROCESS_SUPPORTED', 'PROCESS_CONTRADICTED', 'PROCESS_UNCLEAR'


# ---------------------------------------------------------------------------
# Dominance index weights
# ---------------------------------------------------------------------------
DOMINANCE_WEIGHTS: Dict[str, float] = {
    "xg": 0.40,
    "shots_total": 0.25,
    "possession_pct": 0.15,
    "corners": 0.10,
    "passes_attempted": 0.10,
}


def compute_dominance_index(
    home_stats: Dict[str, Any], away_stats: Dict[str, Any]
) -> Dict[str, Optional[float]]:
    """Compute dominance index (0-1 scale) for each team.

    Uses weighted ratio of key stats. A team with 60% xG share, 55% shot share,
    and 58% possession would score ~0.55-0.60.

    Args:
        home_stats: Normalized home team stats.
        away_stats: Normalized away team stats.

    Returns:
        Dict with 'home' and 'away' dominance scores.
    """
    home_score_parts = []
    away_score_parts = []
    total_weight = 0.0

    for field, weight in DOMINANCE_WEIGHTS.items():
        home_val = home_stats.get(field)
        away_val = away_stats.get(field)
        if home_val is None or away_val is None:
            continue
        try:
            h = float(home_val)
            a = float(away_val)
            if field == "possession_pct":
                # Possession is already a percentage for home; away ≈ 100-home
                home_score_parts.append(weight * (h / 100.0))
                away_score_parts.append(weight * (a / 100.0))
            else:
                denom = h + a
                if denom == 0:
                    home_score_parts.append(weight * 0.5)
                    away_score_parts.append(weight * 0.5)
                else:
                    home_score_parts.append(weight * (h / denom))
                    away_score_parts.append(weight * (a / denom))
            total_weight += weight
        except (ValueError, TypeError):
            continue

    if total_weight == 0:
        return {"home": None, "away": None}

    return {
        "home": round(sum(home_score_parts) / total_weight, 4),
        "away": round(sum(away_score_parts) / total_weight, 4),
    }


def evaluate_process(
    match_id: int,
    predicted_home_xg: Optional[float],
    predicted_away_xg: Optional[float],
    home_stats: Dict[str, Any],
    away_stats: Dict[str, Any],
    outcome_correct: bool,
    predicted_winner: Optional[str] = None,
) -> ProcessEvalResult:
    """Run a full process evaluation for one match.

    Args:
        match_id: Match ID from wc26_schedule.
        predicted_home_xg: DC-predicted home xG.
        predicted_away_xg: DC-predicted away xG.
        home_stats: Normalized home team statistics.
        away_stats: Normalized away team statistics.
        outcome_correct: Was the final result prediction correct?
        predicted_winner: 'home', 'away', or 'draw' from the model.

    Returns:
        ProcessEvalResult with all computed fields.
    """
    result = ProcessEvalResult(
        match_id=match_id,
        predicted_home_xg=predicted_home_xg,
        predicted_away_xg=predicted_away_xg,
        actual_home_xg=_safe_float(home_stats.get("xg")),
        actual_away_xg=_safe_float(away_stats.get("xg")),
        actual_home_goals=_safe_int(home_stats.get("goals")),
        actual_away_goals=_safe_int(away_stats.get("goals")),
        outcome_correct=outcome_correct,
    )

    # --- xG error ---
    if result.predicted_home_xg is not None and result.actual_home_xg is not None:
        result.xg_home_error = round(result.actual_home_xg - result.predicted_home_xg, 4)
    if result.predicted_away_xg is not None and result.actual_away_xg is not None:
        result.xg_away_error = round(result.actual_away_xg - result.predicted_away_xg, 4)

    if result.xg_home_error is not None and result.xg_away_error is not None:
        result.xg_mae = round(
            (abs(result.xg_home_error) + abs(result.xg_away_error)) / 2, 4
        )

    # --- xG direction ---
    if result.actual_home_xg is not None and result.actual_away_xg is not None:
        actual_xg_winner = _winner(result.actual_home_xg, result.actual_away_xg)
        predicted_xg_winner = (
            _winner(result.predicted_home_xg, result.predicted_away_xg)
            if result.predicted_home_xg is not None
            else None
        )
        if predicted_xg_winner is not None:
            result.xg_direction_correct = 1 if predicted_xg_winner == actual_xg_winner else 0
            result.process_correct = bool(result.xg_direction_correct)

    # --- xG vs outcome alignment ---
    if result.xg_direction_correct is not None and predicted_winner is not None:
        actual_xg_winner = _winner(result.actual_home_xg, result.actual_away_xg)
        if predicted_winner == actual_xg_winner:
            result.xg_result_alignment = "aligned"
        else:
            result.xg_result_alignment = "contradicted"
    else:
        result.xg_result_alignment = "unclear"

    # --- Process label ---
    if result.xg_direction_correct == 1 and result.outcome_correct:
        result.process_label = "PROCESS_SUPPORTED"
    elif result.xg_direction_correct == 1 and not result.outcome_correct:
        result.process_label = "PROCESS_SUPPORTED"  # xG was right, result was unlucky
    elif result.xg_direction_correct == 0 and result.outcome_correct:
        result.process_label = "PROCESS_CONTRADICTED"  # Lucky result
    elif result.xg_direction_correct == 0 and not result.outcome_correct:
        result.process_label = "PROCESS_CONTRADICTED"  # Both process and result wrong
    else:
        result.process_label = "PROCESS_UNCLEAR"

    # --- Finishing deltas ---
    if result.actual_home_xg is not None and result.actual_home_goals is not None:
        result.finishing_delta_home = round(result.actual_home_goals - result.actual_home_xg, 4)
    if result.actual_away_xg is not None and result.actual_away_goals is not None:
        result.finishing_delta_away = round(result.actual_away_goals - result.actual_away_xg, 4)

    # --- Shot volume deltas ---
    # NOT_IMPLEMENTED: shot volume prediction requires expected-shot models not yet built.
    # Currently both read from the same actual stats source; replace pred_home_shots with
    # snapshot-derived expected shots when available.
    pred_home_shots = home_stats.get("shots_total")
    act_home_shots = home_stats.get("shots_total")
    if pred_home_shots is not None and act_home_shots is not None:
        result.shot_volume_delta_home = int(act_home_shots) - int(pred_home_shots)
    pred_away_shots = away_stats.get("shots_total")
    act_away_shots = away_stats.get("shots_total")
    if pred_away_shots is not None and act_away_shots is not None:
        result.shot_volume_delta_away = int(act_away_shots) - int(pred_away_shots)

    # --- Dominance index ---
    dom = compute_dominance_index(home_stats, away_stats)
    result.dominance_index_home = dom["home"]
    result.dominance_index_away = dom["away"]

    if result.dominance_index_home is not None and result.dominance_index_away is not None:
        if result.dominance_index_home > result.dominance_index_away + 0.05:
            result.process_winner = "home"
        elif result.dominance_index_away > result.dominance_index_home + 0.05:
            result.process_winner = "away"
        else:
            result.process_winner = "draw"

    # --- Total goals ---
    if result.predicted_home_xg is not None and result.predicted_away_xg is not None:
        result.predicted_total_goals = round(
            result.predicted_home_xg + result.predicted_away_xg, 4
        )
    if result.actual_home_xg is not None and result.actual_away_xg is not None:
        result.actual_total_xg = round(
            result.actual_home_xg + result.actual_away_xg, 4
        )
    if result.predicted_total_goals is not None and result.actual_total_xg is not None:
        result.total_xg_error = round(
            result.actual_total_xg - result.predicted_total_goals, 4
        )

    return result


def _winner(home_val, away_val) -> str:
    if home_val is None or away_val is None:
        return "unknown"
    if home_val > away_val + 0.05:
        return "home"
    elif away_val > home_val + 0.05:
        return "away"
    else:
        return "draw"
