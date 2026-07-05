from types import SimpleNamespace

import pytest

from app.services.prediction_kernel_adapter import run_prediction_kernel_from_components


def test_prediction_kernel_adapter_wraps_pipeline_components():
    result = run_prediction_kernel_from_components(
        home_team="Alpha",
        away_team="Beta",
        competition="FIFA World Cup 2026",
        stage="Group",
        is_neutral=True,
        dc_pred={
            "home_win_prob": 0.50,
            "draw_prob": 0.25,
            "away_win_prob": 0.25,
            "home_xg": 1.5,
            "away_xg": 0.9,
            "score_matrix": [[0.05, 0.04], [0.08, 0.06]],
        },
        dc_weight=0.55,
        enhancer_probs={"home_win_prob": 0.45, "draw_prob": 0.30, "away_win_prob": 0.25},
        weibull_probs={"home_win_prob": 0.48, "draw_prob": 0.28, "away_win_prob": 0.24},
        weibull_weight=0.05,
        elo_pred=SimpleNamespace(home_win_prob=0.52, draw_prob=0.24, away_win_prob=0.24),
        elo_weight=0.15,
        pi_pred={"home_win_prob": 0.49, "draw_prob": 0.27, "away_win_prob": 0.24},
        pi_weight=0.10,
    )

    probs = result.probs.to_short()
    assert sum(probs.values()) == pytest.approx(1.0)
    assert result.provenance["component_status"] == {
        "dc": "used",
        "enhancer": "used",
        "weibull": "used",
        "elo": "used",
        "pi": "used",
    }
    assert result.provenance["weights"]["dc"] == pytest.approx(0.55)


def test_prediction_kernel_adapter_allows_missing_shadow_components():
    result = run_prediction_kernel_from_components(
        home_team="Alpha",
        away_team="Beta",
        competition="Friendly",
        stage="",
        is_neutral=True,
        dc_pred={
            "home_win_prob": 0.40,
            "draw_prob": 0.30,
            "away_win_prob": 0.30,
            "home_xg": 1.1,
            "away_xg": 1.0,
        },
        dc_weight=1.0,
    )

    assert sum(result.probs.to_short().values()) == pytest.approx(1.0)
    assert set(result.provenance["component_status"]) == {"dc"}
