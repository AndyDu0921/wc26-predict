"""Unit tests for weighted conformal prediction (pure functions)."""

from __future__ import annotations

import math

import pytest

from app.core.conformal_core import (
    CONFORMAL_ALPHA,
    WEIGHTED_CONFORMAL_PREDICTION_ENABLED,
    nonconformity_score,
    recency_weight,
    compute_prediction_set,
)


# ── Feature flag default ──────────────────────────────────────────────────

def test_feature_flag_defaults_to_false():
    assert WEIGHTED_CONFORMAL_PREDICTION_ENABLED is False, (
        "WEIGHTED_CONFORMAL_PREDICTION_ENABLED must default to False"
    )


# ── nonconformity_score ───────────────────────────────────────────────────

def test_nonconformity_perfect_prediction():
    """P(true_class) = 1.0 → score = 0.0 (perfectly conforming)."""
    assert nonconformity_score(1.0) == pytest.approx(0.0)

def test_nonconformity_completely_wrong():
    """P(true_class) = 0.0 → score ≈ 1.0."""
    assert nonconformity_score(0.0) == pytest.approx(1.0)

def test_nonconformity_mid_range():
    assert nonconformity_score(0.6) == pytest.approx(0.4)

def test_nonconformity_clips_out_of_range():
    """Values outside [0, 1] are clipped."""
    assert nonconformity_score(1.5) == pytest.approx(0.0)
    assert nonconformity_score(-0.5) == pytest.approx(1.0)

def test_nonconformity_range():
    """Score always in [0, 1]."""
    for p in [0.0, 0.25, 0.5, 0.75, 1.0]:
        s = nonconformity_score(p)
        assert 0.0 <= s <= 1.0, f"nonconformity_score({p}) = {s} is out of range"


# ── recency_weight ────────────────────────────────────────────────────────

def test_same_day_weight_is_one():
    """Same timestamp → weight = 1.0."""
    t = 1_750_000_000.0
    assert recency_weight(t, t) == pytest.approx(1.0)

def test_one_halflife_weight_is_half():
    """After exactly one halflife, weight = 0.5."""
    t_now = 1_750_000_000.0
    t_old = t_now - 30.0 * 86400.0  # 30 days ago
    assert recency_weight(t_now, t_old, halflife_days=30.0) == pytest.approx(0.5)

def test_two_halflives_weight_is_quarter():
    t_now = 1_750_000_000.0
    t_old = t_now - 60.0 * 86400.0  # 60 days ago
    assert recency_weight(t_now, t_old, halflife_days=30.0) == pytest.approx(0.25)

def test_recency_weight_monotonic_decreasing():
    """Older observations get smaller weights."""
    t_now = 1_750_000_000.0
    w_recent = recency_weight(t_now, t_now - 10.0 * 86400.0)
    w_old = recency_weight(t_now, t_now - 50.0 * 86400.0)
    assert w_recent > w_old

def test_recency_weight_never_negative():
    t_now = 1_750_000_000.0
    t_very_old = 0.0  # Unix epoch
    w = recency_weight(t_now, t_very_old)
    assert w > 0.0

def test_recency_weight_never_exceeds_one():
    """Weight ≤ 1.0 always."""
    t = 1_750_000_000.0
    for delta_days in [0, 1, 7, 30, 365]:
        w = recency_weight(t, t - delta_days * 86400.0)
        assert 0.0 < w <= 1.0 + 1e-9, f"weight={w} for delta={delta_days}d"


# ── compute_prediction_set ────────────────────────────────────────────────

def test_below_min_calibration_size_returns_all_three():
    """Not enough calibration data → prediction set = {0,1,2}."""
    result = compute_prediction_set(
        class_probs=[0.5, 0.3, 0.2],
        calibration_scores=[0.1, 0.2],
    )
    assert result["set_size"] == 3
    assert result["prediction_set"] == [0, 1, 2]


def test_prediction_set_deterministic():
    """Same inputs → same outputs."""
    scores = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0,
              0.15, 0.25, 0.35, 0.45, 0.55]
    r1 = compute_prediction_set([0.5, 0.3, 0.2], scores)
    r2 = compute_prediction_set([0.5, 0.3, 0.2], scores)
    assert r1 == r2


def test_high_confidence_model_tight_set():
    """When the model is very confident and calibrated, set may be size 1."""
    # Calibration: model was always very confident AND correct (score ≈ 0)
    scores = [0.0, 0.01, 0.0, 0.02, 0.0, 0.01, 0.0, 0.0, 0.01, 0.02,
              0.0, 0.0, 0.0, 0.01, 0.0]
    result = compute_prediction_set(
        class_probs=[0.90, 0.07, 0.03],
        calibration_scores=scores,
    )
    # With tiny calibration scores, threshold ≈ 0 → only outcomes with P ≈ 1 make it
    assert result["set_size"] >= 1, "Should always include at least one class"


def test_low_confidence_model_wide_set():
    """When calibration shows high variance, set may include multiple classes."""
    scores = [0.6, 0.5, 0.7, 0.55, 0.65, 0.5, 0.6, 0.55, 0.7, 0.5,
              0.6, 0.55, 0.65, 0.7, 0.6]
    result = compute_prediction_set(
        class_probs=[0.5, 0.3, 0.2],
        calibration_scores=scores,
    )
    assert result["set_size"] >= 1


def test_adjusted_probs_sum_to_one():
    """Calibrated probabilities within the set sum to 1.0."""
    scores = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0,
              0.15, 0.25, 0.35, 0.45, 0.55]
    result = compute_prediction_set(
        class_probs=[0.50, 0.30, 0.20],
        calibration_scores=scores,
    )
    total = sum(result["adjusted_probs"])
    assert math.isclose(total, 1.0, rel_tol=1e-9), f"Sum={total}, should be 1.0"


def test_weights_affect_threshold():
    """Weighted calibration should produce different threshold than uniform."""
    scores = [0.1, 0.3, 0.5, 0.7, 0.9, 0.1, 0.3, 0.5, 0.7, 0.9,
              0.1, 0.3, 0.5, 0.7, 0.9]
    uniform = compute_prediction_set([0.5, 0.3, 0.2], scores, None)
    # Weight recent (low) scores higher → threshold should be lower
    weighted = compute_prediction_set(
        [0.5, 0.3, 0.2], scores,
        calibration_weights=[10.0, 1.0, 1.0, 1.0, 1.0,
                             10.0, 1.0, 1.0, 1.0, 1.0,
                             10.0, 1.0, 1.0, 1.0, 1.0],
    )
    # Heavily weighting the low scores (0.1) should lower the threshold
    assert weighted["threshold"] <= uniform["threshold"] + 1e-9, (
        f"weighted threshold {weighted['threshold']} should be ≤ uniform {uniform['threshold']}"
    )


def test_all_output_keys_present():
    result = compute_prediction_set(
        [0.5, 0.3, 0.2],
        [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    )
    expected_keys = {"prediction_set", "adjusted_probs", "threshold", "coverage", "set_size"}
    assert expected_keys <= set(result.keys()), f"Missing keys: {expected_keys - set(result.keys())}"
    assert result["coverage"] == pytest.approx(1.0 - CONFORMAL_ALPHA)
