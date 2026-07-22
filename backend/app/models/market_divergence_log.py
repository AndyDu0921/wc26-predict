"""MarketDivergenceLog — records model vs market divergence for learning.

Tracks when the model and market disagree significantly (>12pp),
then records who was closer after the match finishes.
"""

from __future__ import annotations

from sqlalchemy import Float, Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, JSONVariant


class MarketDivergenceLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Each row captures one instance of significant model-market divergence."""

    __tablename__ = "market_divergence_log"

    match_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )

    # Divergence at prediction time
    divergence_magnitude: Mapped[float] = mapped_column(Float, nullable=False)
    model_home_prob: Mapped[float] = mapped_column(Float, nullable=False)
    market_home_prob: Mapped[float] = mapped_column(Float, nullable=False)

    # Post-match: who was right?
    actual_result: Mapped[str | None] = mapped_column(String(5))  # "H", "D", "A"
    model_was_closer: Mapped[bool | None] = mapped_column(Boolean)

    # Context tags for grouping (derby, must_win, etc.)
    context_tags: Mapped[dict | None] = mapped_column(JSONVariant)
