"""V4.9 accuracy-engine audit models."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

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


class EvidenceItem(TimestampMixin, Base):
    """Traceable pre-match evidence for the V4.10 information-state engine."""

    __tablename__ = "evidence_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    evidence_key: Mapped[str] = mapped_column(String(96), nullable=False, unique=True, index=True)
    match_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    home_team: Mapped[str | None] = mapped_column(String(100), nullable=True)
    away_team: Mapped[str | None] = mapped_column(String(100), nullable=True)
    evidence_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text_hash: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    reliability_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    metadata_payload: Mapped[dict | None] = mapped_column("metadata", JSONVariant, nullable=True)


class InformationStateSignal(TimestampMixin, Base):
    """Structured shadow signal extracted from traceable evidence."""

    __tablename__ = "information_state_signals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    signal_key: Mapped[str] = mapped_column(String(96), nullable=False, unique=True, index=True)
    match_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    team: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    player: Mapped[str | None] = mapped_column(String(100), nullable=True)
    signal_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    magnitude: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence_ids: Mapped[list[str]] = mapped_column(JSONVariant, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="shadow", index=True)
    source_status: Mapped[str] = mapped_column(String(32), nullable=False, default="used_pre_match")
    shadow_adjustment: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_payload: Mapped[dict | None] = mapped_column("metadata", JSONVariant, nullable=True)


class SignalEvaluation(TimestampMixin, Base):
    """Post-match attribution for an information-state signal."""

    __tablename__ = "signal_evaluations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    evaluation_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    match_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    prediction_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    signal_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actual_outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    verdict: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    contribution_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
