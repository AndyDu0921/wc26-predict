"""PredictionResult — standardized prediction output dataclass.

Replaces the ad-hoc dict returned by snapshot.py/run_snapshot().
Provides backward-compatible .to_dict() for existing consumers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from app.version import VERSION

from app.services.evaluation_sample import evaluation_sample_from_prediction_dict
from app.services.weights import WeightConfig


DegradedSeverity = Literal["warning", "error"]
SourceStatusValue = Literal["used", "unavailable", "failed", "skipped"]


@dataclass
class DegradedReason:
    """Structured record of a data source that degraded during prediction.

    Contract (Ticket 1.2):
        - source: The data source that failed (e.g. "pi_rating", "market_calibration")
        - reason: Why it failed (e.g. "fitting_failed", "api_unavailable")
        - severity: "warning" (prediction continues) or "error" (data completely missing)
        - detail: Optional human-readable detail (e.g. exception message)
    """

    source: str
    reason: str
    severity: DegradedSeverity = "warning"
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "reason": self.reason,
            "severity": self.severity,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "DegradedReason":
        return cls(
            source=data.get("source", ""),
            reason=data.get("reason", ""),
            severity=data.get("severity", "warning"),  # type: ignore[arg-type]
            detail=data.get("detail", ""),
        )


@dataclass
class SourceStatus:
    """Structured status for a data source used by the prediction pipeline.

    This is the hard contract that prevents optional real-time sources from
    disappearing silently. A source can be used, unavailable, failed, or skipped,
    and consumers must render that state explicitly.
    """

    status: SourceStatusValue
    reason: str = ""
    detail: str = ""
    attempted: bool = False
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "detail": self.detail,
            "attempted": self.attempted,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceStatus":
        status = data.get("status", "skipped")
        if status not in ("used", "unavailable", "failed", "skipped"):
            status = "skipped"
        return cls(
            status=status,  # type: ignore[arg-type]
            reason=str(data.get("reason", "")),
            detail=str(data.get("detail", "")),
            attempted=bool(data.get("attempted", False)),
            required=bool(data.get("required", False)),
        )


@dataclass
class PredictionResult:
    """Standardized output from PredictionPipeline.predict_match().

    Contains all probabilities, xG, component breakdowns, and metadata
    needed by downstream consumers (snapshot store, report renderer, API).
    """

    # ── Match identification ──
    home_team: str
    away_team: str
    competition: str
    match_id: str = ""
    is_neutral: bool = False
    match_date: str = ""  # ISO format date string
    stage: str = ""

    # ── Core probabilities (must sum to 1.0) ──
    home_win_prob: float = 0.33
    draw_prob: float = 0.34
    away_win_prob: float = 0.33

    # ── Expected goals ──
    home_xg: float = 1.0
    away_xg: float = 1.0

    # ── Component breakdown (before fusion) ──
    dc_probs: dict[str, float] = field(default_factory=dict)
    enhancer_probs: dict[str, float] | None = None
    elo_probs: dict[str, float] | None = None
    pi_probs: dict[str, float] | None = None
    weibull_probs: dict[str, float] | None = None
    market_probs: dict[str, Any] | None = None

    # ── Elo ratings ──
    home_elo: float = 1500.0
    away_elo: float = 1500.0
    elo_gap: float = 0.0

    # ── Top scores ──
    top_scores: list[dict[str, object]] = field(default_factory=list)
    score_matrix: list[list[float]] = field(default_factory=list)
    source_score_matrices: dict[str, list[list[float]]] = field(default_factory=dict)

    # ── Over/Under distribution ──
    over_under: dict[str, float] = field(default_factory=dict)

    # ── Metadata ──
    model_version: str = VERSION
    weight_config: WeightConfig | None = None
    mode: str = "internal_research"
    as_of: str = ""  # ISO format
    generated_at: str = ""  # ISO format
    confidence: str = "medium"
    risk_tags: list[str] = field(default_factory=list)
    confidence_penalty: float = 0.0

    # ── Pipeline trace ──
    components_used: list[str] = field(default_factory=list)
    missing_inputs: list[str] = field(default_factory=list)
    pipeline_params: dict[str, object] = field(default_factory=dict)

    # ── Signal / Context ──
    active_events: list[dict[str, object]] = field(default_factory=list)
    context_adjustments: list[dict[str, object]] = field(default_factory=list)
    market_applied: bool = False
    market_weight_used: float = 0.0
    divergence: float = 0.0

    # ── Weibull ──
    weibull_applied: bool = False

    # ── NegBin (V4.3.0 S4) ──
    negbin_applied: bool = False
    negbin_probs: dict[str, float] | None = None

    # ── Elo details ──
    elo_detail: dict[str, object] = field(default_factory=dict)

    # ── Calibration ──
    calibration_monitor: dict[str, object] = field(default_factory=dict)
    calibration_applied: bool = False  # R4-C7: true when isotonic calibration was applied

    # ── Sources ──
    sources: dict[str, str] = field(default_factory=dict)
    source_status: dict[str, SourceStatus] = field(default_factory=dict)

    # ── Degraded reasons (Ticket 1.2 contract) ──
    # Always present — empty list when all data sources succeeded.
    # Each entry is a DegradedReason with source/reason/severity/detail.
    degraded_reasons: list[DegradedReason] = field(default_factory=list)

    # ── A3: Stacking Meta-Learner (V4.5) ──
    stacking_result: dict[str, Any] | None = None

    # ── B1: Weighted Conformal Prediction (V4.5) ──
    conformal_result: dict[str, Any] | None = None

    # ── Derived ──
    @property
    def favorite(self) -> str:
        if self.home_win_prob > self.away_win_prob:
            return self.home_team
        return self.away_team

    @property
    def confidence_score(self) -> float:
        return max(0.0, 1.0 - self.confidence_penalty)

    # ── Backward-compatible dict export ──
    def to_dict(self) -> dict[str, Any]:
        """Convert to dict matching the snapshot.py result format.

        This is the bridge between new PredictionResult and existing
        consumers: save_prediction_snapshot(), render_markdown(), etc.
        """
        wc = self.weight_config
        payload = {
            "meta": {
                "match_id": self.match_id,
                "home_team": self.home_team,
                "away_team": self.away_team,
                "competition": self.competition,
                "is_neutral": self.is_neutral,
                "match_date": self.match_date,
                "stage": self.stage,
                "model_version": self.model_version,
                "mode": self.mode,
                "as_of": self.as_of,
                "generated_at": self.generated_at,
                "components_used": self.components_used,
                "weight_config": wc.to_dict() if wc else {},
            },
            "prediction": {
                "home_win_prob": self.home_win_prob,
                "draw_prob": self.draw_prob,
                "away_win_prob": self.away_win_prob,
                "home_xg": self.home_xg,
                "away_xg": self.away_xg,
                "confidence": self.confidence,
                "confidence_penalty": self.confidence_penalty,
                "risk_tags": self.risk_tags,
                "top_scores": self.top_scores,
                "score_matrix": self.score_matrix,
                "source_score_matrices": self.source_score_matrices,
                "over_under": self.over_under,
                "market_applied": self.market_applied,
                "market_weight_used": self.market_weight_used,
                "divergence": self.divergence,
                "weibull_applied": self.weibull_applied,
                "negbin_applied": self.negbin_applied,
            },
            "component_probs": {
                "dc": self.dc_probs,
                "enhancer": self.enhancer_probs,
                "negbin": self.negbin_probs if self.negbin_applied else None,
                "elo": self.elo_probs,
                "pi_rating": self.pi_probs,
                "weibull": self.weibull_probs,
                "market": self.market_probs,
            },
            "elo": {
                "home_elo": self.home_elo,
                "away_elo": self.away_elo,
                "elo_gap": self.elo_gap,
                "detail": self.elo_detail,
            },
            "calibration_monitor": self.calibration_monitor,
            "calibration_applied": self.calibration_applied,
            "pipeline_params": self.pipeline_params,
            "stacking_result": self.stacking_result,
            "conformal_result": self.conformal_result,
            "missing_inputs": self.missing_inputs,
            "active_event_ids": [e.get("id", "") for e in self.active_events],
            "context_adjustments": self.context_adjustments,
            "sources": self.sources,
            "source_status": {
                key: value.to_dict() if isinstance(value, SourceStatus) else value
                for key, value in self.source_status.items()
            },
            "degraded_reasons": [dr.to_dict() for dr in self.degraded_reasons],
        }
        payload["evaluation_sample"] = evaluation_sample_from_prediction_dict(payload)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PredictionResult":
        """Create a PredictionResult from a snapshot.py-style result dict.

        Useful for backward-compatibility when reading from DB or old code.
        """
        meta = data.get("meta", {})
        pred = data.get("prediction", {})
        comp = data.get("component_probs", {})
        elo = data.get("elo", {})

        return cls(
            home_team=str(meta.get("home_team", "")),
            match_id=str(meta.get("match_id", "")),
            away_team=str(meta.get("away_team", "")),
            competition=str(meta.get("competition", "")),
            is_neutral=bool(meta.get("is_neutral", False)),
            match_date=str(meta.get("match_date", "")),
            stage=str(meta.get("stage", "")),
            home_win_prob=float(pred.get("home_win_prob", 0.33)),
            draw_prob=float(pred.get("draw_prob", 0.34)),
            away_win_prob=float(pred.get("away_win_prob", 0.33)),
            home_xg=float(pred.get("home_xg", 1.0)),
            away_xg=float(pred.get("away_xg", 1.0)),
            dc_probs=comp.get("dc", {}),
            enhancer_probs=comp.get("enhancer"),
            elo_probs=comp.get("elo"),
            pi_probs=comp.get("pi_rating"),
            weibull_probs=comp.get("weibull"),
            market_probs=comp.get("market"),
            home_elo=float(elo.get("home_elo", 1500)),
            away_elo=float(elo.get("away_elo", 1500)),
            elo_gap=float(elo.get("elo_gap", 0)),
            top_scores=list(pred.get("top_scores", [])),
            score_matrix=list(pred.get("score_matrix", [])),
            source_score_matrices=dict(pred.get("source_score_matrices", {})),
            over_under=dict(pred.get("over_under", {})),
            confidence=str(pred.get("confidence", "medium")),
            risk_tags=list(pred.get("risk_tags", [])),
            confidence_penalty=float(pred.get("confidence_penalty", 0)),
            components_used=list(meta.get("components_used", [])),
            missing_inputs=list(data.get("missing_inputs", [])),
            pipeline_params=dict(data.get("pipeline_params", {})),
            active_events=list(data.get("active_event_ids", [])),
            context_adjustments=list(data.get("context_adjustments", [])),
            market_applied=bool(pred.get("market_applied", False)),
            market_weight_used=float(pred.get("market_weight_used", 0)),
            divergence=float(pred.get("divergence", 0)),
            weibull_applied=bool(pred.get("weibull_applied", False)),
            negbin_applied=bool(pred.get("negbin_applied", False)),
            negbin_probs=comp.get("negbin"),
            elo_detail=dict(elo.get("detail", {})),
            calibration_monitor=dict(data.get("calibration_monitor", {})),
            calibration_applied=bool(data.get("calibration_applied", False)),
            sources=dict(data.get("sources", {})),
            source_status={
                key: SourceStatus.from_dict(value) if isinstance(value, dict) else value
                for key, value in data.get("source_status", {}).items()
            },
            degraded_reasons=[
                DegradedReason.from_dict(dr) if isinstance(dr, dict) else dr
                for dr in data.get("degraded_reasons", [])
            ],
            model_version=str(meta.get("model_version", VERSION)),
            mode=str(meta.get("mode", "internal_research")),
            as_of=str(meta.get("as_of", "")),
            generated_at=str(meta.get("generated_at", "")),
            stacking_result=data.get("stacking_result"),
            conformal_result=data.get("conformal_result"),
        )
