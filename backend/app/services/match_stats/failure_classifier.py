"""Failure classifier: assigns a model_failure_type and learning_weight to every match.

Labels:
  GOOD_PREDICTION       — outcome correct + process supported
  LUCKY_RESULT          — outcome correct but process contradicted
  UNLUCKY_RESULT        — outcome wrong but process supported model
  MODEL_INPUT_ERROR     — venue/Elo/injury/lineup input problem
  MODEL_STRUCTURE_ERROR — xG/process direction clearly wrong
  MARKET_UNDERWEIGHTED  — high-consensus market correct but fusion didn't adopt
  WEIBULL_EXTREME_ERROR — Weibull extreme value misled
  PI_OVERREACTION       — Pi single-upset over-weighted
  EVENT_DISTORTED       — red card / early penalty / early injury altered match
  DATA_QUALITY_FAILURE  — missing or conflicting data
  UNKNOWN               — manual review required
"""

from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Label definitions and base learning weights
# ---------------------------------------------------------------------------
LEARNING_WEIGHT_BY_LABEL: Dict[str, float] = {
    "GOOD_PREDICTION": 1.00,
    "MODEL_STRUCTURE_ERROR": 1.00,   # Most valuable signal — model was wrong
    "MARKET_UNDERWEIGHTED": 0.90,    # Strong signal for weight adjustment
    "WEIBULL_EXTREME_ERROR": 0.90,   # 0.80→0.90: KO extreme bias is diagnostically valuable
    "PI_OVERREACTION": 0.70,
    "MODEL_INPUT_ERROR": 0.50,       # Fix the input, not the model
    "LUCKY_RESULT": 0.50,            # 0.30→0.50: KO "luck" often reflects real uncaptured competitive factors
    "UNLUCKY_RESULT": 0.30,          # Model was right, result was random
    "EVENT_DISTORTED": 0.20,         # Red card/penalty — low signal
    "DATA_QUALITY_FAILURE": 0.00,    # Garbage in, don't learn
    "UNKNOWN": 0.00,                 # Requires manual review
}

# Learning weight tiers
LEARNING_TIER_FULL = 0.70    # Eligible to generate a gated shadow proposal.
LEARNING_TIER_DIAGNOSTIC = 0.30  # Record for stats, don't propose changes
# Below 0.30: record only, no learning


def classify_failure(
    outcome_correct: bool,
    xg_direction_correct: Optional[int],
    xg_mae: Optional[float],
    data_quality_score: float,
    match_context: Optional[Dict[str, Any]] = None,
    component_signals: Optional[Dict[str, bool]] = None,
    outcome_verified: bool = True,
) -> Dict[str, Any]:
    """Classify the failure mode for a completed match.

    Two independent layers are assessed:
      * OUTCOME layer — was the H/D/A prediction right? This is learnable
        whenever the final score is independently verified, regardless of
        whether match statistics (xG/shots) were collected.
      * PROCESS layer — xG direction / dominance attribution. This needs
        reliable match statistics (``data_quality_score >= 0.65`` and xG).

    ``DATA_QUALITY_FAILURE`` (zero learning) is reserved for the case where the
    OUTCOME itself cannot be trusted — an unverified score or a multi-source
    conflict on the result. Missing match STATISTICS alone no longer discards a
    match: outcome-level learning proceeds (weight naturally attenuated by
    ``data_quality_score``) and only the process-level attribution is withheld.

    Args:
        outcome_correct: Was the H/D/A prediction correct?
        xg_direction_correct: 1 if predicted xG winner == actual xG winner, 0 if not, None if unknown.
        xg_mae: Mean absolute error of predicted vs actual xG.
        data_quality_score: 0-1 quality score for the match STATISTICS.
        match_context: Optional dict with flags like:
            venue_home_advantage_missed (bool)
            elo_default_value (bool) — one team had Elo=1500 default
            red_card_before_minute (int or None)
            penalty_before_minute (int or None)
            early_injury_minute (int or None)
            home_team_travel_advantage (str or None)
            outcome_conflict (bool) — multi-source disagreement on the final score
        component_signals: Optional dict with flags:
            market_high_consensus_correct (bool) — 8+ bookmakers all correct, fusion ignored
            weibull_extreme_wrong (bool)
            pi_single_upset_overreaction (bool)
        outcome_verified: Is the final score independently verified? Post-match
            callers always have a verified score, so this defaults to True. Pass
            False only when the outcome itself is unknown/disputed.

    Returns:
        Dict with 'model_failure_type', 'base_learning_weight', 'reason',
        'process_verified'.
    """
    ctx = match_context or {}
    sig = component_signals or {}

    # --- Gate 1: Outcome trustworthiness ---
    # Only an untrustworthy OUTCOME zeroes out learning — not missing statistics.
    if not outcome_verified or ctx.get("outcome_conflict"):
        return _result("DATA_QUALITY_FAILURE", _check_unknown=False)

    # --- Gate 2: Event distortion ---
    red_before = ctx.get("red_card_before_minute")
    pen_before = ctx.get("penalty_before_minute")
    injury_early = ctx.get("early_injury_minute")
    if (red_before is not None and red_before < 60) or \
       (pen_before is not None and pen_before < 30) or \
       (injury_early is not None and injury_early < 30):
        return _result("EVENT_DISTORTED", _check_unknown=False)

    # --- Gate 3: Input problems ---
    if ctx.get("venue_home_advantage_missed"):
        return _result("MODEL_INPUT_ERROR")
    if ctx.get("elo_default_value"):
        return _result("MODEL_INPUT_ERROR")

    # --- Gate 4: Component-specific failures ---
    if sig.get("market_high_consensus_correct"):
        return _result("MARKET_UNDERWEIGHTED")
    if sig.get("weibull_extreme_wrong"):
        return _result("WEIBULL_EXTREME_ERROR")
    if sig.get("pi_single_upset_overreaction"):
        return _result("PI_OVERREACTION")

    # --- Gate 5: Process-based classification ---
    # Requires reliable match STATISTICS (quality gate) AND xG data.
    stats_reliable = data_quality_score >= 0.65
    if stats_reliable and xg_direction_correct is not None and xg_mae is not None:
        if outcome_correct and xg_direction_correct == 1 and xg_mae <= 0.45:
            return _result("GOOD_PREDICTION")
        if outcome_correct and (xg_direction_correct == 0 or xg_mae > 0.75):
            return _result("LUCKY_RESULT")
        if not outcome_correct and xg_direction_correct == 1 and xg_mae <= 0.45:
            return _result("UNLUCKY_RESULT")
        if xg_mae > 0.75 or xg_direction_correct == 0:
            return _result("MODEL_STRUCTURE_ERROR")

    # --- Gate 6: Outcome-only learning (process data unavailable/unreliable) ---
    # The score is verified but statistics/xG are missing, so we can only judge
    # the outcome. process_verified=False keeps this out of the full learning
    # tier and flags the report; the weight is further attenuated by data quality.
    if outcome_correct:
        return _result("GOOD_PREDICTION", process_verified=False)
    return _result("MODEL_STRUCTURE_ERROR", process_verified=False)


def compute_learning_weight(
    model_failure_type: str,
    data_quality_score: float,
    snapshot_complete: bool = True,
    match_context: Optional[Dict[str, Any]] = None,
    process_verified: bool = True,
) -> float:
    """Compute the final learning weight for a match.

    learning_weight = base_weight_by_label * data_quality_score * snapshot_factor

    Args:
        model_failure_type: From classify_failure().
        data_quality_score: 0-1 from quality module.
        snapshot_complete: Is the prediction snapshot complete?
        match_context: Additional context (future use).
        process_verified: Whether process-level attribution was verified. When
            False (outcome-only learning), the weight is capped below the full
            tier so an unverified-process match can never drive a production
            weight proposal.

    Returns:
        Learning weight 0.0-1.0.
    """
    base = LEARNING_WEIGHT_BY_LABEL.get(model_failure_type, 0.0)
    snapshot_factor = 1.0 if snapshot_complete else 0.3

    weight = base * data_quality_score * snapshot_factor

    # Apply context-specific penalties
    ctx = match_context or {}
    if ctx.get("elo_default_value"):
        weight *= 0.7  # Data quality concern
    if ctx.get("multi_source_conflict"):
        weight *= 0.6

    # Outcome-only learning must not reach the full (proposal-eligible) tier.
    if not process_verified:
        weight = min(weight, LEARNING_TIER_FULL - 0.05)

    return round(max(0.0, min(1.0, weight)), 4)


def get_learning_tier(weight: float) -> str:
    """Map learning weight to an action tier."""
    if weight >= LEARNING_TIER_FULL:
        return "full"
    elif weight >= LEARNING_TIER_DIAGNOSTIC:
        return "diagnostic"
    else:
        return "record_only"


def _result(label: str, _check_unknown: bool = True, process_verified: bool = True) -> Dict[str, Any]:
    base = LEARNING_WEIGHT_BY_LABEL.get(label, 0.0)
    reason = label if process_verified else f"{label} (outcome-only; process unverified)"
    return {
        "model_failure_type": label,
        "base_learning_weight": base,
        "reason": reason,
        "process_verified": process_verified,
    }
