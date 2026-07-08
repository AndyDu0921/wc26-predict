"""Tests for process_evaluator and failure_classifier."""
import pytest
from app.services.match_stats.process_evaluator import (
    compute_dominance_index,
    evaluate_process,
    ProcessEvalResult,
)
from app.services.match_stats.failure_classifier import (
    classify_failure,
    compute_learning_weight,
    get_learning_tier,
    LEARNING_WEIGHT_BY_LABEL,
    LEARNING_TIER_FULL,
)


class TestDominanceIndex:
    def test_clear_dominance(self):
        """MEX-ECU style: one team clearly dominant."""
        home = {"xg": 1.80, "shots_total": 15, "possession_pct": 58, "corners": 8, "passes_attempted": 450}
        away = {"xg": 0.30, "shots_total": 5, "possession_pct": 42, "corners": 2, "passes_attempted": 320}
        result = compute_dominance_index(home, away)
        assert result["home"] is not None
        assert result["away"] is not None
        assert result["home"] > 0.55  # Home should dominate
        assert result["away"] < 0.45

    def test_even_match(self):
        home = {"xg": 1.0, "shots_total": 10, "possession_pct": 50, "corners": 5, "passes_attempted": 400}
        away = {"xg": 1.0, "shots_total": 10, "possession_pct": 50, "corners": 5, "passes_attempted": 400}
        result = compute_dominance_index(home, away)
        assert abs(result["home"] - 0.5) < 0.05
        assert abs(result["away"] - 0.5) < 0.05

    def test_missing_fields(self):
        home = {"xg": 1.5, "shots_total": 12}
        away = {"xg": 0.5, "shots_total": 4}
        result = compute_dominance_index(home, away)
        # Should still work with partial data
        assert result["home"] is not None
        assert result["away"] is not None
        assert result["home"] > 0.5

    def test_all_missing(self):
        home = {}
        away = {}
        result = compute_dominance_index(home, away)
        assert result["home"] is None
        assert result["away"] is None


class TestProcessEvaluator:
    def test_mex_ecu_style(self):
        """MEX-ECU: DC predicted 0.53 xG, actual was 1.80 — massive error."""
        home_stats = {"xg": 1.80, "shots_total": 15, "shots_on_target": 3, "goals": 2}
        away_stats = {"xg": 0.30, "shots_total": 5, "shots_on_target": 1, "goals": 0}

        result = evaluate_process(
            match_id=183,
            predicted_home_xg=0.53,
            predicted_away_xg=0.29,
            home_stats=home_stats,
            away_stats=away_stats,
            outcome_correct=False,  # Predicted draw, actual MEX win
            predicted_winner="draw",
        )

        # xG errors
        assert result.xg_home_error == pytest.approx(1.27, abs=0.01)
        assert result.xg_away_error == pytest.approx(0.01, abs=0.01)
        assert result.xg_mae == pytest.approx(0.64, abs=0.01)

        # Direction — DC xG was 0.53 vs 0.29, so DC directionally had MEX ahead
        # The xG DIRECTION was correct (MEX > ECU), but MAGNITUDE was way off (0.53 vs 1.80)
        assert result.xg_direction_correct == 1  # Predicted xG winner (home) == actual xG winner (home)
        assert result.predicted_total_goals == pytest.approx(0.82, abs=0.01)
        assert result.actual_total_xg == pytest.approx(2.10, abs=0.01)
        assert result.total_xg_error == pytest.approx(1.28, abs=0.01)
        # Outcome was wrong (predicted draw, MEX won), xG direction was right but underpowered
        assert result.process_label == "PROCESS_SUPPORTED"  # xG direction was correct even if magnitude wrong

        # Finishing (goals - xG)
        assert result.finishing_delta_home == pytest.approx(0.20, abs=0.01)  # 2 - 1.80
        assert result.finishing_delta_away == pytest.approx(-0.30, abs=0.01)  # 0 - 0.30
        # Dominance — home should be clearly dominant
        assert result.dominance_index_home is not None
        assert result.dominance_index_home > 0.70
        assert result.process_winner == "home"

    def test_good_prediction(self):
        """FRA-SWE style: prediction correct, process supports it."""
        home_stats = {"xg": 2.20, "shots_total": 18, "shots_on_target": 8, "goals": 3}
        away_stats = {"xg": 0.40, "shots_total": 4, "shots_on_target": 1, "goals": 0}

        result = evaluate_process(
            match_id=184,
            predicted_home_xg=2.20,
            predicted_away_xg=0.80,
            home_stats=home_stats,
            away_stats=away_stats,
            outcome_correct=True,
            predicted_winner="home",
        )

        assert result.xg_direction_correct == 1
        assert result.process_label == "PROCESS_SUPPORTED"

    def test_no_xg_data(self):
        """When xG is missing, should still work with available data."""
        home_stats = {"shots_total": 15, "goals": 2}
        away_stats = {"shots_total": 5, "goals": 0}

        result = evaluate_process(
            match_id=183,
            predicted_home_xg=None,
            predicted_away_xg=None,
            home_stats=home_stats,
            away_stats=away_stats,
            outcome_correct=False,
            predicted_winner="draw",
        )

        assert result.xg_mae is None
        assert result.xg_direction_correct is None
        assert result.process_label == "PROCESS_UNCLEAR"
        # Dominance should still work
        assert result.dominance_index_home is not None

    def test_shot_volume_delta_requires_predicted_shots(self):
        home_stats = {"xg": 1.0, "shots_total": 15, "goals": 1}
        away_stats = {"xg": 0.8, "shots_total": 7, "goals": 0}

        result = evaluate_process(
            match_id=185,
            predicted_home_xg=1.1,
            predicted_away_xg=0.7,
            home_stats=home_stats,
            away_stats=away_stats,
            outcome_correct=True,
            predicted_winner="home",
        )

        assert result.shot_volume_delta_home is None
        assert result.shot_volume_delta_away is None
        assert result.shot_volume_delta_reason == "expected_shots_unavailable"

    def test_shot_volume_delta_uses_expected_shots_when_available(self):
        home_stats = {"xg": 1.0, "shots_total": 15, "goals": 1}
        away_stats = {"xg": 0.8, "shots_total": 7, "goals": 0}

        result = evaluate_process(
            match_id=186,
            predicted_home_xg=1.1,
            predicted_away_xg=0.7,
            home_stats=home_stats,
            away_stats=away_stats,
            outcome_correct=True,
            predicted_winner="home",
            predicted_home_shots=10,
            predicted_away_shots=9,
        )

        assert result.shot_volume_delta_home == 5
        assert result.shot_volume_delta_away == -2
        assert result.shot_volume_delta_reason == "expected_shots_available"


class TestFailureClassifier:
    def test_good_prediction(self):
        result = classify_failure(
            outcome_correct=True,
            xg_direction_correct=1,
            xg_mae=0.25,
            data_quality_score=0.90,
        )
        assert result["model_failure_type"] == "GOOD_PREDICTION"
        assert result["base_learning_weight"] == 1.0

    def test_lucky_result(self):
        result = classify_failure(
            outcome_correct=True,
            xg_direction_correct=0,  # xG said other team should have won
            xg_mae=0.80,
            data_quality_score=0.85,
        )
        assert result["model_failure_type"] == "LUCKY_RESULT"
        assert result["base_learning_weight"] == 0.50  # V4.8.1: 0.30→0.50 for KO context

    def test_unlucky_result(self):
        result = classify_failure(
            outcome_correct=False,
            xg_direction_correct=1,  # xG model was right!
            xg_mae=0.30,
            data_quality_score=0.85,
        )
        assert result["model_failure_type"] == "UNLUCKY_RESULT"
        assert result["base_learning_weight"] == 0.30

    def test_model_structure_error(self):
        result = classify_failure(
            outcome_correct=False,
            xg_direction_correct=0,
            xg_mae=1.50,
            data_quality_score=0.80,
        )
        assert result["model_failure_type"] == "MODEL_STRUCTURE_ERROR"
        assert result["base_learning_weight"] == 1.0

    def test_data_quality_failure(self):
        # DATA_QUALITY_FAILURE now means the OUTCOME itself is untrustworthy
        # (unverified score), not merely missing statistics.
        result = classify_failure(
            outcome_correct=False,
            xg_direction_correct=0,
            xg_mae=1.0,
            data_quality_score=0.50,
            outcome_verified=False,
        )
        assert result["model_failure_type"] == "DATA_QUALITY_FAILURE"
        assert result["base_learning_weight"] == 0.0

    def test_outcome_conflict_is_data_quality_failure(self):
        # A multi-source conflict on the final score also zeroes out learning.
        result = classify_failure(
            outcome_correct=True,
            xg_direction_correct=1,
            xg_mae=0.30,
            data_quality_score=0.90,
            match_context={"outcome_conflict": True},
        )
        assert result["model_failure_type"] == "DATA_QUALITY_FAILURE"

    def test_missing_stats_but_outcome_correct_still_learns(self):
        # #195 CAN-MAR scenario: score verified (0-3) and direction correct,
        # but no xG/stats collected (data_quality_score low). Outcome-level
        # learning must proceed at a reduced (diagnostic) weight — not be
        # discarded as DATA_QUALITY_FAILURE.
        result = classify_failure(
            outcome_correct=True,
            xg_direction_correct=None,
            xg_mae=None,
            data_quality_score=0.35,
        )
        assert result["model_failure_type"] == "GOOD_PREDICTION"
        assert result["process_verified"] is False
        lw = compute_learning_weight(
            "GOOD_PREDICTION", 0.35, snapshot_complete=True,
            process_verified=result["process_verified"],
        )
        assert lw == pytest.approx(0.35)
        assert get_learning_tier(lw) == "diagnostic"

    def test_missing_stats_but_outcome_wrong(self):
        # Verified score, wrong prediction, no process data → conservative
        # MODEL_STRUCTURE_ERROR flagged as process-unverified.
        result = classify_failure(
            outcome_correct=False,
            xg_direction_correct=None,
            xg_mae=None,
            data_quality_score=0.35,
        )
        assert result["model_failure_type"] == "MODEL_STRUCTURE_ERROR"
        assert result["process_verified"] is False

    def test_outcome_only_weight_capped_below_full_tier(self):
        # Even with high data quality, outcome-only learning cannot reach the
        # full (proposal-eligible) tier.
        lw = compute_learning_weight(
            "GOOD_PREDICTION", 0.95, snapshot_complete=True, process_verified=False,
        )
        assert lw < LEARNING_TIER_FULL
        assert get_learning_tier(lw) != "full"

    def test_event_distorted_red_card(self):
        result = classify_failure(
            outcome_correct=False,
            xg_direction_correct=0,
            xg_mae=0.80,
            data_quality_score=0.90,
            match_context={"red_card_before_minute": 35},
        )
        assert result["model_failure_type"] == "EVENT_DISTORTED"
        assert result["base_learning_weight"] == 0.20

    def test_market_underweighted(self):
        result = classify_failure(
            outcome_correct=False,
            xg_direction_correct=None,
            xg_mae=None,
            data_quality_score=0.90,
            component_signals={"market_high_consensus_correct": True},
        )
        assert result["model_failure_type"] == "MARKET_UNDERWEIGHTED"
        assert result["base_learning_weight"] == 0.90

    def test_model_input_error_venue(self):
        result = classify_failure(
            outcome_correct=False,
            xg_direction_correct=None,
            xg_mae=None,
            data_quality_score=0.90,
            match_context={"venue_home_advantage_missed": True},
        )
        assert result["model_failure_type"] == "MODEL_INPUT_ERROR"
        assert result["base_learning_weight"] == 0.50

    def test_mex_ecu_realistic(self):
        """MEX-ECU real scenario: venue home advantage missed + high-consensus market."""
        result = classify_failure(
            outcome_correct=False,
            xg_direction_correct=0,
            xg_mae=0.64,
            data_quality_score=0.85,
            match_context={
                "venue_home_advantage_missed": True,
                "elo_default_value": False,
            },
            component_signals={
                "market_high_consensus_correct": True,
                "weibull_extreme_wrong": True,
                "pi_single_upset_overreaction": True,
            },
        )
        # Venue missed takes priority (catches MODEL_INPUT_ERROR before component checks)
        assert result["model_failure_type"] == "MODEL_INPUT_ERROR"


class TestLearningWeight:
    def test_full_weight(self):
        w = compute_learning_weight("GOOD_PREDICTION", 1.0, snapshot_complete=True)
        assert w == 1.0

    def test_record_only(self):
        w = compute_learning_weight("DATA_QUALITY_FAILURE", 0.5, snapshot_complete=True)
        assert w == 0.0

    def test_incomplete_snapshot(self):
        w = compute_learning_weight("GOOD_PREDICTION", 1.0, snapshot_complete=False)
        assert w < 0.50  # 30% reduction for incomplete snapshot

    def test_tiers(self):
        assert get_learning_tier(1.0) == "full"
        assert get_learning_tier(0.80) == "full"
        assert get_learning_tier(0.70) == "full"
        assert get_learning_tier(0.50) == "diagnostic"
        assert get_learning_tier(0.30) == "diagnostic"
        assert get_learning_tier(0.29) == "record_only"
        assert get_learning_tier(0.0) == "record_only"

    def test_all_labels_have_weights(self):
        for label in ["GOOD_PREDICTION", "MODEL_STRUCTURE_ERROR",
                       "MARKET_UNDERWEIGHTED", "WEIBULL_EXTREME_ERROR",
                       "PI_OVERREACTION", "MODEL_INPUT_ERROR",
                       "UNLUCKY_RESULT", "LUCKY_RESULT",
                       "EVENT_DISTORTED", "DATA_QUALITY_FAILURE", "UNKNOWN"]:
            assert label in LEARNING_WEIGHT_BY_LABEL, f"Missing: {label}"
