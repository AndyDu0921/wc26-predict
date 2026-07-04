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
