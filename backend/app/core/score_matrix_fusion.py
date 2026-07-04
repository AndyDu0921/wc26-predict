"""Shared score-matrix fusion for prediction paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.engine import fuse_score_matrices, negbin_score_matrix
from app.services.score_matrix_calibrator import calibrate_score_matrix


@dataclass(frozen=True)
class ScoreMatrixFusionResult:
    score_matrix: list[list[float]] | None
    top_scores: list[dict[str, Any]] | None
    diagnostics: dict[str, Any]
    source_score_matrices: dict[str, list[list[float]]] = field(default_factory=dict)


def build_score_matrix_fusion(
    *,
    raw_score_matrix: list[list[float]] | None,
    final_probs: dict[str, float],
    home_xg: float,
    away_xg: float,
    tau_rho: float = -0.30,
    weibull_score_matrix: list[list[float]] | None = None,
    max_goals: int = 5,
) -> ScoreMatrixFusionResult:
    """Fuse DC, NegBin, and optional Weibull score matrices once."""
    if not raw_score_matrix:
        return ScoreMatrixFusionResult(
            score_matrix=None,
            top_scores=None,
            diagnostics={"calibration_applied": False, "fusion_sources": []},
            source_score_matrices={},
        )

    diagnostics: dict[str, Any] = {"calibration_applied": False, "fusion_sources": []}
    matrices: list[list[list[float]]] = []
    weights: list[float] = []
    source_matrices: dict[str, list[list[float]]] = {"dc": raw_score_matrix}

    matrices.append(raw_score_matrix)
    weights.append(0.40)
    diagnostics["fusion_sources"].append("dc")

    if home_xg > 0 and away_xg > 0:
        nb_mat = negbin_score_matrix(home_xg, away_xg, max_g=max_goals, tau_rho=tau_rho)
        matrices.append(nb_mat)
        weights.append(0.35)
        source_matrices["negbin"] = nb_mat
        diagnostics["fusion_sources"].append("negbin")

    if weibull_score_matrix is not None:
        matrices.append(weibull_score_matrix)
        weights.append(0.25)
        source_matrices["weibull"] = weibull_score_matrix
        diagnostics["fusion_sources"].append("weibull")

    if len(matrices) >= 2:
        fused = fuse_score_matrices(matrices, weights, final_probs=final_probs)
        diagnostics["calibration_applied"] = True
        return ScoreMatrixFusionResult(
            score_matrix=fused,
            top_scores=_top_scores(fused),
            diagnostics=diagnostics,
            source_score_matrices=source_matrices,
        )

    calibrated = calibrate_score_matrix(raw_matrix=raw_score_matrix, final_probs=final_probs)
    return ScoreMatrixFusionResult(
        score_matrix=calibrated["calibrated_matrix"],
        top_scores=calibrated["top3_scores"],
        diagnostics=calibrated,
        source_score_matrices=source_matrices,
    )


def _top_scores(matrix: list[list[float]]) -> list[dict[str, Any]]:
    flat: list[tuple[int, int, float]] = []
    for home_g, row in enumerate(matrix):
        for away_g, prob in enumerate(row):
            flat.append((home_g, away_g, float(prob)))
    return [
        {"score": f"{home_g}:{away_g}", "prob": round(prob, 4)}
        for home_g, away_g, prob in sorted(flat, key=lambda item: item[2], reverse=True)[:3]
    ]
