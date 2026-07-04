"""V4.8 accuracy-engine audit models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONVariant, TimestampMixin, UUIDPrimaryKeyMixin


class FeatureSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable feature payload used by an experiment or prediction candidate."""

    __tablename__ = "feature_snapshots"

    sample_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    match_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="evaluation_registry")
    as_of_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    kickoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    horizon_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    feature_hash: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    data_availability: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    leakage_status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)


class ExperimentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Shadow experiment output.  Never applies a model or weight change."""

    __tablename__ = "experiment_runs"

    experiment_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    candidate_name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    champion_name: Mapped[str] = mapped_column(String(80), nullable=False, default="current_fusion")
    sample_registry_hash: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    n_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics_current: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    metrics_candidate: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    paired_deltas: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    group_metrics: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    leakage_checks: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    gate_decision: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class CandidatePrediction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-sample shadow candidate prediction for paired analysis."""

    __tablename__ = "candidate_predictions"

    experiment_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sample_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    candidate_name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    actual_result: Mapped[str | None] = mapped_column(String(10), nullable=True)
    current_probs: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    candidate_probs: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    component_payload: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)


class ModelChangeProposal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Generic proposal ledger for weights, calibrators, models, and rules."""

    __tablename__ = "model_change_proposals"

    proposal_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    candidate_name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="self_evolution")
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="proposal_pending", index=True)
    sample_registry_hash: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    target_table: Mapped[str | None] = mapped_column(String(80), nullable=True)
    target_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    base_payload: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    candidate_payload: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    metrics: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    gate_decision: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    evidence: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
