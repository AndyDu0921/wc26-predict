"""Shadow model candidate registry.

The registry documents research candidates that may improve accuracy, while
making the rollout rule explicit: candidates are offline/shadow only until a
paired walk-forward experiment passes the gate and a human approves it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


PROPER_SCORING_METRICS = ("brier", "logloss", "rps")
SCORE_METRICS = ("score_logloss", "top3_hit_rate")


@dataclass(frozen=True)
class ModelCandidateSpec:
    candidate_name: str
    family: str
    purpose: str
    required_data: tuple[str, ...]
    evaluation_metrics: tuple[str, ...]
    production_enabled: bool = False
    shadow_only: bool = True
    rollout_gate: str = "paired_walk_forward_backtest_gate"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CANDIDATE_SPECS: tuple[ModelCandidateSpec, ...] = (
    ModelCandidateSpec(
        candidate_name="dynamic_dixon_coles",
        family="dynamic_poisson",
        purpose="Time-varying attack/defence strengths with DC low-score correction.",
        required_data=("historical_results", "kickoff_time", "team_identity"),
        evaluation_metrics=PROPER_SCORING_METRICS + SCORE_METRICS,
        notes="Candidate inspired by dynamic football score models; no production effect.",
    ),
    ModelCandidateSpec(
        candidate_name="dynamic_bivariate_poisson",
        family="dynamic_bivariate_poisson",
        purpose="Joint score model with time-varying team abilities and goal correlation.",
        required_data=("historical_results", "kickoff_time", "team_identity"),
        evaluation_metrics=PROPER_SCORING_METRICS + SCORE_METRICS,
        notes="Offline benchmark only until it beats the champion on paired metrics.",
    ),
    ModelCandidateSpec(
        candidate_name="bayesian_weighted_dynamic",
        family="bayesian_dynamic",
        purpose="Bayesian weighted evolution for attack and defence strengths.",
        required_data=("historical_results", "kickoff_time", "team_identity"),
        evaluation_metrics=PROPER_SCORING_METRICS + SCORE_METRICS,
        notes="Research candidate for static-strength drift; requires leakage checks.",
    ),
    ModelCandidateSpec(
        candidate_name="covariate_ml_baseline",
        family="covariate_machine_learning",
        purpose="Random-forest/gradient-boosting style benchmark over auditable pre-match covariates.",
        required_data=("feature_snapshots", "actual_result", "sample_registry_hash"),
        evaluation_metrics=PROPER_SCORING_METRICS + ("ece",),
        notes="Unavailable until enough leak-free feature snapshots exist; shadow-only benchmark.",
    ),
    ModelCandidateSpec(
        candidate_name="dirichlet_calibration",
        family="probability_calibration",
        purpose="Native multiclass calibration for home/draw/away probabilities.",
        required_data=("paired_prediction_samples", "actual_result"),
        evaluation_metrics=PROPER_SCORING_METRICS + ("ece",),
        notes="Calibration tournament candidate; cannot pass on ECE alone.",
    ),
    ModelCandidateSpec(
        candidate_name="stacking_optimizer",
        family="ensemble_fusion",
        purpose="Proper-scoring optimized stacking over component probability distributions.",
        required_data=("component_probabilities", "actual_result", "sample_registry_hash"),
        evaluation_metrics=PROPER_SCORING_METRICS + ("ece",),
        notes="Replaces direction-only model selection with paired proper scoring.",
    ),
)


def list_shadow_candidate_models() -> list[dict[str, Any]]:
    return [spec.to_dict() for spec in CANDIDATE_SPECS]


def get_shadow_candidate_model(candidate_name: str) -> dict[str, Any]:
    for spec in CANDIDATE_SPECS:
        if spec.candidate_name == candidate_name:
            return spec.to_dict()
    raise KeyError(f"Unknown shadow model candidate: {candidate_name}")
