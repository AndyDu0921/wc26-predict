"""Adapters from pipeline component outputs into the pure prediction kernel."""

from __future__ import annotations

from typing import Any

from app.core.prediction_kernel import (
    ComponentPrediction,
    KernelFeatureSnapshot,
    MatchContext,
    PredictionKernel,
    PredictionKernelResult,
    ProbabilityDistribution,
)


def run_prediction_kernel_from_components(
    *,
    home_team: str,
    away_team: str,
    competition: str,
    stage: str,
    is_neutral: bool,
    dc_pred: dict[str, Any],
    dc_weight: float,
    enhancer_probs: dict[str, Any] | None = None,
    weibull_probs: dict[str, Any] | None = None,
    weibull_weight: float = 0.0,
    elo_pred: Any | None = None,
    elo_weight: float = 0.0,
    pi_pred: dict[str, Any] | None = None,
    pi_weight: float = 0.0,
    as_of_time: str | None = None,
    kickoff_at: str | None = None,
) -> PredictionKernelResult:
    """Run the V4.9 kernel using the component shapes produced by the pipeline."""
    components = _build_kernel_components(
        dc_pred=dc_pred,
        enhancer_probs=enhancer_probs,
        weibull_probs=weibull_probs,
        weibull_weight=weibull_weight,
        elo_pred=elo_pred,
        pi_pred=pi_pred,
    )
    return PredictionKernel().run(
        context=MatchContext(
            home_team=home_team,
            away_team=away_team,
            competition=competition,
            stage=stage,
            is_neutral=is_neutral,
            as_of_time=as_of_time,
            kickoff_at=kickoff_at,
        ),
        feature_snapshot=KernelFeatureSnapshot(
            components=components,
            dc_home_xg=float(dc_pred.get("home_xg", 0)),
            dc_away_xg=float(dc_pred.get("away_xg", 0)),
            weights={
                "dc": float(dc_weight),
                "weibull": float(weibull_weight),
                "elo": float(elo_weight),
                "pi": float(pi_weight) if pi_pred is not None else 0.0,
            },
        ),
    )


def _build_kernel_components(
    *,
    dc_pred: dict[str, Any],
    enhancer_probs: dict[str, Any] | None,
    weibull_probs: dict[str, Any] | None,
    weibull_weight: float,
    elo_pred: Any | None,
    pi_pred: dict[str, Any] | None,
) -> dict[str, ComponentPrediction]:
    components = {
        "dc": ComponentPrediction(
            name="dc",
            probs=ProbabilityDistribution.from_mapping(dc_pred),
            score_matrix=dc_pred.get("score_matrix"),
            source_status="used",
        )
    }
    if enhancer_probs is not None:
        components["enhancer"] = ComponentPrediction(
            name="enhancer",
            probs=ProbabilityDistribution.from_mapping(enhancer_probs),
            source_status="used",
        )
    if weibull_probs is not None and weibull_weight > 0:
        components["weibull"] = ComponentPrediction(
            name="weibull",
            probs=ProbabilityDistribution.from_mapping(weibull_probs),
            source_status="used",
        )
    if elo_pred is not None:
        components["elo"] = ComponentPrediction(
            name="elo",
            probs=ProbabilityDistribution.from_mapping(_elo_to_probs(elo_pred)),
            source_status="used",
        )
    if pi_pred is not None:
        components["pi"] = ComponentPrediction(
            name="pi",
            probs=ProbabilityDistribution.from_mapping(pi_pred),
            source_status="used",
        )
    return components


def _elo_to_probs(elo_pred: Any) -> dict[str, float]:
    return {
        "home_win_prob": float(elo_pred.home_win_prob),
        "draw_prob": float(elo_pred.draw_prob),
        "away_win_prob": float(elo_pred.away_win_prob),
    }
