"""MatchResultVerification — audit trail of source claims about match scores.

Each row is either:
- A single source claim: "AFA says score is 3-0, match is FT"
- A consensus row: "2+ sources agree the score is 3-0"

Consensus rows have is_consensus=True. Source rows that contributed to a
consensus have their consensus_for_id pointing to the consensus row.

This is NOT a replacement for MatchResult — it is an audit trail that
verifies whether the score in match_results is trustworthy before it
enters the learning pipeline.
"""

from __future__ import annotations
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MatchResultVerification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "match_result_verification"

    match_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("matches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    home_goals: Mapped[int] = mapped_column(Integer, nullable=False)
    away_goals: Mapped[int] = mapped_column(Integer, nullable=False)

    # Source identity
    source_name: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # e.g. "AFA", "FIFA", "ESPN", "api_football"
    source_tier: Mapped[int] = mapped_column(
        Integer, nullable=False
    )  # 1=official federation … 6=other

    # Match status as reported by this source
    match_status_at_source: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "FT", "Finished", "Final"

    # Consensus marker
    is_consensus: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # Self-referential FK: source rows point to the consensus row
    consensus_for_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("match_result_verification.id"),
        nullable=True,
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationship back to the consensus row (for source rows)
    consensus_for = relationship(
        "MatchResultVerification",
        remote_side="MatchResultVerification.id",
        foreign_keys=[consensus_for_id],
        uselist=False,
    )
