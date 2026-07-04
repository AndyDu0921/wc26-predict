"""Auditable model-weight proposal records."""

from __future__ import annotations

from sqlalchemy import Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONVariant, TimestampMixin, UUIDPrimaryKeyMixin


class ModelWeightProposal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Candidate weight changes that passed or failed the backtest gate.

    These rows are intentionally proposals only.  They never mutate
    ``model_weight_config`` by themselves.
    """

    __tablename__ = "model_weight_proposals"

    competition: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    stage: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="gate_pending",
        server_default="gate_pending",
        index=True,
    )

    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fold_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_weight_delta: Mapped[float] = mapped_column(Float, nullable=False)
    min_improvement: Mapped[float] = mapped_column(Float, nullable=False)

    base_weights: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    candidate_weights: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    gate_decision: Mapped[dict] = mapped_column(JSONVariant, nullable=False)
    evidence: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
