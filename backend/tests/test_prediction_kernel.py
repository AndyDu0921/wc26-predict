import pytest

from app.core.engine import run_core_fusion
from app.core.prediction_kernel import (
    ComponentPrediction,
    KernelFeatureSnapshot,
    MatchContext,
    PredictionKernel,
    ProbabilityDistribution,
)


def test_prediction_kernel_matches_core_fusion():
    dc = {"home_win_prob": 0.50, "draw_prob": 0.25, "away_win_prob": 0.25, "home_xg": 1.4, "away_xg": 0.9}
    enhancer = {"home_win_prob": 0.45, "draw_prob": 0.30, "away_win_prob": 0.25}
    elo = {"home_win_prob": 0.48, "draw_prob": 0.27, "away_win_prob": 0.25}
    pi = {"home_win_prob": 0.44, "draw_prob": 0.28, "away_win_prob": 0.28}

    expected = run_core_fusion(
        dc_probs=dc,
        dc_home_xg=1.4,
        dc_away_xg=0.9,
        dc_base_weight=0.9,
        enh_probs=enhancer,
        weibull_probs=None,
        weibull_weight=0.0,
        elo_probs=elo,
        elo_weight=0.12,
        pi_probs=pi,
        pi_weight=0.17,
    )
    result = PredictionKernel().run(
        context=MatchContext(
            home_team="Alpha",
            away_team="Beta",
            competition="FIFA World Cup 2026",
        ),
        feature_snapshot=KernelFeatureSnapshot(
            components={
                "dc": ComponentPrediction("dc", ProbabilityDistribution.from_mapping(dc), source_status="used"),
                "enhancer": ComponentPrediction("enhancer", ProbabilityDistribution.from_mapping(enhancer), source_status="used"),
                "elo": ComponentPrediction("elo", ProbabilityDistribution.from_mapping(elo), source_status="used"),
                "pi": ComponentPrediction("pi", ProbabilityDistribution.from_mapping(pi), source_status="used"),
            },
            dc_home_xg=1.4,
            dc_away_xg=0.9,
            weights={"dc": 0.9, "elo": 0.12, "pi": 0.17, "weibull": 0.0},
        ),
    )

    assert result.probs.to_long() == pytest.approx(expected.probs)
    assert result.core_fusion.negbin_applied is True
    assert result.provenance["schema_version"] == "prediction_kernel.v1"
    assert len(result.provenance["feature_hash"]) == 64


def test_prediction_kernel_requires_dc_component():
    with pytest.raises(ValueError):
        PredictionKernel().run(
            context=MatchContext("Alpha", "Beta", "Cup"),
            feature_snapshot=KernelFeatureSnapshot(components={}, dc_home_xg=1.0, dc_away_xg=1.0, weights={}),
        )
