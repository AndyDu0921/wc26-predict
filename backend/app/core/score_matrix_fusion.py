"""Shared score-matrix fusion for prediction paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

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
    weights.append(0.45)                    # DC 0.40→0.45: Poisson+τ is reliable baseline
    diagnostics["fusion_sources"].append("dc")

    if home_xg > 0 and away_xg > 0:
        nb_mat = negbin_score_matrix(home_xg, away_xg, max_g=max_goals, tau_rho=tau_rho)
        matrices.append(nb_mat)
        weights.append(0.38)                # NegBin 0.35→0.38: overdispersion correction validated
        source_matrices["negbin"] = nb_mat
        diagnostics["fusion_sources"].append("negbin")

    if weibull_score_matrix is not None:
        weibull_quality = score_matrix_quality_gate(
            weibull_score_matrix,
            home_xg=home_xg,
            away_xg=away_xg,
            source="weibull",
        )
        diagnostics["weibull_score_matrix_quality"] = weibull_quality
        source_matrices["weibull"] = weibull_score_matrix
        if not weibull_quality["used"]:
            diagnostics.setdefault("shadow_sources", []).append("weibull")
        else:
            matrices.append(weibull_score_matrix)
            weights.append(0.17)                # Weibull 0.25→0.17: bimodal, unreliable for score prediction
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


def score_matrix_quality_gate(
    matrix: list[list[float]],
    *,
    home_xg: float,
    away_xg: float,
    source: str,
) -> dict[str, Any]:
    """Return whether a source score matrix is safe for score fusion.

    The gate is intentionally conservative for non-DC sources: it allows the
    matrix to be stored for audit, but shadows it when the distribution is too
    sparse or a single scoreline dominates. This prevents pathological Weibull
    matrices from creating unrealistic top-score outputs while preserving the
    source evidence for post-match review.
    """
    arr = np.asarray(matrix, dtype=float)
    if arr.ndim != 2 or arr.shape[0] == 0 or arr.shape[1] == 0:
        return {
            "source": source,
            "used": False,
            "reason": "invalid_shape",
        }

    total = float(arr.sum())
    if total <= 0:
        return {
            "source": source,
            "used": False,
            "reason": "non_positive_total",
            "sum": total,
        }

    probs = arr / total
    max_cell = float(probs.max())
    nonzero_share = float((probs > 1e-12).sum() / probs.size)
    top_idx = np.unravel_index(int(probs.argmax()), probs.shape)
    top_home = int(top_idx[0])
    top_away = int(top_idx[1])
    expected_gap = float(home_xg - away_xg)
    top_gap = float(top_home - top_away)
    gap_inconsistent = (
        abs(expected_gap) >= 0.25
        and top_gap != 0
        and (expected_gap > 0) != (top_gap > 0)
    )

    reasons: list[str] = []
    if max_cell > 0.16:
        reasons.append("max_cell_probability_too_high")
    if nonzero_share < 0.50:
        reasons.append("matrix_too_sparse")
    if gap_inconsistent:
        reasons.append("top_score_direction_conflicts_with_xg")

    return {
        "source": source,
        "used": not reasons,
        "reason": "ok" if not reasons else ",".join(reasons),
        "sum": round(total, 6),
        "max_cell_probability": round(max_cell, 6),
        "nonzero_share": round(nonzero_share, 6),
        "top_score": f"{top_home}:{top_away}",
        "home_xg": round(float(home_xg), 4),
        "away_xg": round(float(away_xg), 4),
    }
