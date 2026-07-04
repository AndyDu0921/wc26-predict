"""Prediction snapshot model — standardized, append-only, traceable."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Float, JSON, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.version import VERSION


class PredictionSnapshot(Base):
    __tablename__ = "prediction_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    match_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    model_version: Mapped[str] = mapped_column(String(32), default=VERSION)
    run_type: Mapped[str] = mapped_column(String(32), default="baseline_v0", index=True)

    home_team: Mapped[str] = mapped_column(String(200))
    away_team: Mapped[str] = mapped_column(String(200))
    competition: Mapped[str] = mapped_column(String(200))
    match_time: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Probabilities (JSON)
    baseline_probs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    component_probs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    market_probs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    adjusted_probs: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    expected_goals: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    top_scores: Mapped[list | None] = mapped_column(JSON, nullable=True)
    fused_score_matrix: Mapped[list | None] = mapped_column(JSON, nullable=True)
    source_score_matrices: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    elo_ratings: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    active_event_ids: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)
    missing_inputs: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)
    confidence: Mapped[str | None] = mapped_column(String(16), nullable=True, default="low")

    # Pipeline metadata
    calibration_monitor: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    pipeline_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Report
    report_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<PredictionSnapshot {self.id[:8]} {self.home_team} vs {self.away_team} [{self.run_type}]>"
