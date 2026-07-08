"""PredictionLearningLog — per-match error attribution for self-evolution.

After each match finishes, this records:
- Overall error magnitude (Brier)
- Which component contributed most to the error
- Whether the model or market was closer
"""

from __future__ import annotations

from sqlalchemy import DateTime, Float, Boolean, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, JSONVariant


class PredictionLearningLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-match learning record — one row per prediction that was evaluated."""

    __tablename__ = "prediction_learning_log"

    match_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    prediction_run_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    snapshot_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )

    # Overall error
    error_magnitude: Mapped[float] = mapped_column(Float, nullable=False)  # Brier
    error_direction: Mapped[str] = mapped_column(String(30))  # overestimate_home, etc.

    # Per-component error attribution (all should sum to ~1.0)
    dc_error_contribution: Mapped[float | None] = mapped_column(Float)
    enhancer_error_contribution: Mapped[float | None] = mapped_column(Float)
    elo_error_contribution: Mapped[float | None] = mapped_column(Float)
    signal_error_contribution: Mapped[float | None] = mapped_column(Float)
    market_error_contribution: Mapped[float | None] = mapped_column(Float)

    # Marginal contributions (leave-one-out): positive = component helped
    dc_marginal: Mapped[float | None] = mapped_column(Float)
    enhancer_marginal: Mapped[float | None] = mapped_column(Float)
    elo_marginal: Mapped[float | None] = mapped_column(Float)
    market_marginal: Mapped[float | None] = mapped_column(Float)
    signal_marginal: Mapped[float | None] = mapped_column(Float)

    # Model vs Market
    model_was_right: Mapped[bool | None] = mapped_column(Boolean)
    divergence_at_prediction: Mapped[float | None] = mapped_column(Float)

    # Verification status
    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        server_default="active",
        nullable=False,
    )
    # Values: "active" (verified, in-use), "pending_review" (awaiting verification),
    #         "invalidated" (wrong result later corrected), "superseded" (replaced by newer verified record),
    #         "legacy_untraceable" / "legacy_ambiguous" (old rows excluded from active learning)

    # ── V4.7-score: score-level evaluation metrics ──
    score_log_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_exact_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    score_top3_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Per-source score log loss for marginal analysis (which matrix source
    # contributed most to score prediction accuracy; Wheatcroft 2021)
    dc_score_log_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    negbin_score_log_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    weibull_score_log_loss: Mapped[float | None] = mapped_column(Float, nullable=True)

    # V4.6-process-eval: learning weight from process evaluation
    # Controls whether this match can drive parameter changes:
    #   >=0.70 "full"       — error attribution + signal/market/context + WeightProposal
    #   0.30-0.70 "diagnostic" — error attribution + logging, no weight changes
    #   <0.30 "record_only"    — basic error log only, no side effects
    learning_weight: Mapped[float] = mapped_column(
        Float, default=1.0, server_default="1.0", nullable=False,
    )
    learning_tier: Mapped[str] = mapped_column(
        String(20), default="full", server_default="full", nullable=False,
    )
    # Values: "full", "diagnostic", "record_only"

    # Context
    context_tags: Mapped[dict | None] = mapped_column(JSONVariant)
    signal_verdicts: Mapped[dict | None] = mapped_column(JSONVariant)

    # V4.11 Match Data OS: post-match-only rich game-state diagnostics.
    # These fields describe the finished match and must not be joined into
    # pre-match strict feature snapshots for the same game.
    game_state_profile: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    comeback_profile: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    event_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
