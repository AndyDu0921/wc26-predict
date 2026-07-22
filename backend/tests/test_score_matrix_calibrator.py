"""Tests for outcome-constrained score matrix calibrator."""


import numpy as np
import pytest

from app.services.score_matrix_calibrator import calibrate_score_matrix


def _make_uniform_matrix(g: int = 5) -> np.ndarray:
    """Return a uniform (g+1)×(g+1) matrix."""
    size = g + 1
    return np.ones((size, size)) / (size * size)


def test_calibrate_preserves_normalisation() -> None:
    """Calibrated matrix should sum to 1."""
    M = _make_uniform_matrix()
    result = calibrate_score_matrix(
        M.tolist(),
        {"home_win_prob": 0.45, "draw_prob": 0.25, "away_win_prob": 0.30},
    )
    calibrated = np.array(result["calibrated_matrix"])
    assert 0.9999 <= calibrated.sum() <= 1.0001
    assert result["calibration_applied"] is True


def test_outcome_consistency_error_is_small() -> None:
    """After calibration, bucket sums must match target within 1e-6."""
    M = _make_uniform_matrix()
    result = calibrate_score_matrix(
        M.tolist(),
        {"home_win_prob": 0.50, "draw_prob": 0.30, "away_win_prob": 0.20},
    )
    after = result["after_outcome_probs"]
    target = result["target_outcome_probs"]
    assert abs(after["home"] - target["home"]) < 1e-6
    assert abs(after["draw"] - target["draw"]) < 1e-6
    assert abs(after["away"] - target["away"]) < 1e-6
    assert result["outcome_consistency_error"] < 1e-6


def test_calibrate_idempotent() -> None:
    """Running calibration twice should not change the matrix further."""
    M = _make_uniform_matrix()
    target = {"home_win_prob": 0.55, "draw_prob": 0.22, "away_win_prob": 0.23}
    r1 = calibrate_score_matrix(M.tolist(), target)
    r2 = calibrate_score_matrix(r1["calibrated_matrix"], target)

    m1 = np.array(r1["calibrated_matrix"])
    m2 = np.array(r2["calibrated_matrix"])
    assert np.allclose(m1, m2, atol=1e-10)


def test_top3_scores_extracted() -> None:
    """Top-3 scorelines must be in descending probability order."""
    M = _make_uniform_matrix()
    result = calibrate_score_matrix(
        M.tolist(),
        {"home_win_prob": 0.45, "draw_prob": 0.25, "away_win_prob": 0.30},
    )
    scores = result["top3_scores"]
    assert len(scores) == 3
    assert scores[0]["prob"] >= scores[1]["prob"] >= scores[2]["prob"]
    for s in scores:
        assert ":" in s["score"]


def test_calibrate_with_realistic_dc_matrix() -> None:
    """Simulate a DC matrix where raw buckets differ from final probs."""
    # Simulate a 6×6 matrix where DC favours home win
    g = 5
    size = g + 1
    M = np.zeros((size, size))
    # Put more probability in home-win cells
    for i in range(size):
        for j in range(size):
            if i > j:
                M[i, j] = 0.04  # home win cells
            elif i == j:
                M[i, j] = 0.02  # draw cells
            else:
                M[i, j] = 0.01  # away win cells
    M /= M.sum()

    # Before calibration, buckets are skewed
    home_mask = np.zeros_like(M, dtype=bool)
    draw_mask = np.zeros_like(M, dtype=bool)
    for i in range(size):
        for j in range(size):
            if i > j:
                home_mask[i, j] = True
            elif i == j:
                draw_mask[i, j] = True

    home_before = float(M[home_mask].sum())
    draw_before = float(M[draw_mask].sum())

    # Target: much higher draw probability
    target = {"home_win_prob": 0.30, "draw_prob": 0.45, "away_win_prob": 0.25}
    result = calibrate_score_matrix(M.tolist(), target)

    after = result["after_outcome_probs"]
    assert after["draw"] > draw_before  # draw should increase
    assert after["home"] < home_before  # home should decrease
    assert abs(after["home"] - 0.30) < 1e-6
    assert abs(after["draw"] - 0.45) < 1e-6


def test_short_form_keys_accepted() -> None:
    """Both 'home_win_prob' and 'home' key conventions should work."""
    M = _make_uniform_matrix()
    result = calibrate_score_matrix(
        M.tolist(),
        {"home": 0.35, "draw": 0.35, "away": 0.30},
    )
    assert result["outcome_consistency_error"] < 1e-6
    after = result["after_outcome_probs"]
    assert abs(after["home"] - 0.35) < 1e-6


def test_empty_bucket_does_not_crash() -> None:
    """A bucket with zero probability should be handled gracefully."""
    g = 5
    size = g + 1
    M = np.ones((size, size))
    # Zero out all away-win cells
    for i in range(size):
        for j in range(size):
            if i < j:
                M[i, j] = 0.0
    M /= M.sum()

    result = calibrate_score_matrix(
        M.tolist(),
        {"home_win_prob": 0.45, "draw_prob": 0.25, "away_win_prob": 0.30},
    )
    # Should not crash and should still normalise
    calibrated = np.array(result["calibrated_matrix"])
    assert 0.9999 <= calibrated.sum() <= 1.0001


def test_raises_on_non_square() -> None:
    """Non-square input should raise ValueError."""
    with pytest.raises(ValueError):
        calibrate_score_matrix(
            [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],
            {"home_win_prob": 0.33, "draw_prob": 0.34, "away_win_prob": 0.33},
        )


def test_raises_on_zero_sum() -> None:
    """All-zero matrix should raise ValueError."""
    with pytest.raises(ValueError):
        calibrate_score_matrix(
            [[0.0, 0.0], [0.0, 0.0]],
            {"home_win_prob": 0.33, "draw_prob": 0.34, "away_win_prob": 0.33},
        )


def test_diagnostics_present() -> None:
    """All expected diagnostic keys should be in the result."""
    M = _make_uniform_matrix()
    result = calibrate_score_matrix(
        M.tolist(),
        {"home_win_prob": 0.45, "draw_prob": 0.25, "away_win_prob": 0.30},
    )
    expected_keys = {
        "calibrated_matrix", "top3_scores",
        "before_outcome_probs", "after_outcome_probs",
        "target_outcome_probs", "outcome_consistency_error",
        "max_cell_change_ratio", "calibration_applied",
    }
    assert expected_keys <= set(result.keys())
