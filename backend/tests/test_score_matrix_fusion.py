import pytest

from app.core.score_matrix_fusion import build_score_matrix_fusion


def test_score_matrix_fusion_adds_negbin_source_and_top_scores():
    raw = [
        [0.20, 0.10, 0.05],
        [0.15, 0.20, 0.05],
        [0.10, 0.10, 0.05],
    ]

    result = build_score_matrix_fusion(
        raw_score_matrix=raw,
        final_probs={"home_win_prob": 0.45, "draw_prob": 0.30, "away_win_prob": 0.25},
        home_xg=1.2,
        away_xg=0.8,
        max_goals=2,
    )

    assert result.score_matrix is not None
    assert result.top_scores
    assert result.diagnostics["calibration_applied"] is True
    assert result.diagnostics["fusion_sources"] == ["dc", "negbin"]
    assert set(result.source_score_matrices) == {"dc", "negbin"}
    assert sum(sum(row) for row in result.score_matrix) == pytest.approx(1.0)


def test_score_matrix_fusion_falls_back_to_single_source_calibration():
    raw = [
        [0.25, 0.25],
        [0.25, 0.25],
    ]

    result = build_score_matrix_fusion(
        raw_score_matrix=raw,
        final_probs={"home_win_prob": 0.50, "draw_prob": 0.25, "away_win_prob": 0.25},
        home_xg=0.0,
        away_xg=0.0,
        max_goals=1,
    )

    assert result.score_matrix is not None
    assert result.diagnostics["calibration_applied"] is True
    assert result.source_score_matrices == {"dc": raw}


def test_score_matrix_fusion_shadows_pathological_weibull_matrix():
    raw = [
        [0.02, 0.05, 0.05, 0.03, 0.02, 0.01],
        [0.04, 0.07, 0.08, 0.06, 0.03, 0.02],
        [0.03, 0.07, 0.07, 0.05, 0.03, 0.01],
        [0.02, 0.04, 0.04, 0.03, 0.02, 0.01],
        [0.01, 0.02, 0.02, 0.01, 0.01, 0.00],
        [0.00, 0.01, 0.01, 0.01, 0.00, 0.00],
    ]
    pathological_weibull = [
        [0.0, 0.0, 0.0, 0.0, 0.0],
        [0.0, 0.4058, 0.0, 0.2046, 0.0],
        [0.0, 0.0, 0.0, 0.0, 0.0],
        [0.0, 0.1800, 0.0, 0.2096, 0.0],
        [0.0, 0.0, 0.0, 0.0, 0.0],
    ]

    result = build_score_matrix_fusion(
        raw_score_matrix=raw,
        final_probs={"home_win_prob": 0.44, "draw_prob": 0.18, "away_win_prob": 0.38},
        home_xg=1.74,
        away_xg=2.11,
        weibull_score_matrix=pathological_weibull,
        max_goals=5,
    )

    assert result.score_matrix is not None
    assert result.source_score_matrices["weibull"] == pathological_weibull
    assert "weibull" not in result.diagnostics["fusion_sources"]
    assert "weibull" in result.diagnostics["shadow_sources"]
    assert result.diagnostics["weibull_score_matrix_quality"]["used"] is False
    assert "max_cell_probability_too_high" in result.diagnostics["weibull_score_matrix_quality"]["reason"]
