"""MotivationEvent model — motivation tags per team per match."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MotivationEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Motivation factor for a team in a specific match.

    Generated from standings data + competition context.
    Uses 5 core tags: HIGH_MOTIVATION, MEDIUM_MOTIVATION, LOW_MOTIVATION,
    MUST_WIN, ROTATION_RISK.
    """

    __tablename__ = "motivation_events"
    __table_args__ = (
        UniqueConstraint("match_id", "team_name", name="uq_motivation_match_team"),
    )

    match_id: Mapped[UUID] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # Core motivation tag (see MOTIVATION_TAGS definition)
    motivation_tag: Mapped[str] = mapped_column(String(30), nullable=False)

    # Numeric strength 0.0-1.0, consumed by the canonical signal path.
    motivation_strength: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)

    # Human-readable explanation
    explanation: Mapped[str] = mapped_column(String(500), default="")

    # Source of the motivation factor
    source: Mapped[str] = mapped_column(String(50), default="standings-derived")

    # Timestamp when this motivation event should be reconsidered
    expires_at: Mapped[str | None] = mapped_column(String(30))


# ── Motivation tag constants ──

MOTIVATION_TAGS = {
    "HIGH_MOTIVATION": {
        "label": "高动力",
        "description": "争冠/欧战区/保级触发，球队有强烈取胜动力",
        "default_strength": 0.8,
        "xG_modifier": 1.05,  # slight boost to xG
    },
    "MEDIUM_MOTIVATION": {
        "label": "中等动力",
        "description": "有目标但非生死战",
        "default_strength": 0.5,
        "xG_modifier": 1.00,
    },
    "LOW_MOTIVATION": {
        "label": "低动力",
        "description": "中游安全区，无欲无求",
        "default_strength": 0.2,
        "xG_modifier": 0.95,
    },
    "MUST_WIN": {
        "label": "必胜",
        "description": "不胜即出局/降级/丧失主动权",
        "default_strength": 1.0,
        "xG_modifier": 1.10,
    },
    "ROTATION_RISK": {
        "label": "轮换风险",
        "description": "已晋级/已降级/赛程密集，可能轮换",
        "default_strength": -0.3,  # negative = suppresses xG
        "xG_modifier": 0.90,
    },
}
