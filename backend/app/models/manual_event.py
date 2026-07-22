"""ManualEvent model — manually injected structured events.

These are user/operator-created events that feed into the Event Ledger
and the information-state/synchronous signal path. Unlike auto-extracted NewsSignals,
these come with explicit human confidence and source attribution.

Supported event types (from feature_flags.yaml):
  INJURY | SUSPENSION | LINEUP_CONFIRMED | LINEUP_RUMOR
  | ROTATION_HINT | MOTIVATION | WEATHER
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# Allowed event types (matches feature_flags.yaml)
ALLOWED_EVENT_TYPES = frozenset({
    "INJURY", "SUSPENSION", "LINEUP_CONFIRMED", "LINEUP_RUMOR",
    "ROTATION_HINT", "MOTIVATION", "WEATHER",
})

ALLOWED_SEVERITIES = frozenset({"low", "medium", "high", "critical"})


class ManualEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A manually-injected structured event for a specific match."""

    __tablename__ = "manual_events"

    # Match reference (optional — can be team-specific without a match)
    match_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("matches.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Which team is affected
    team_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # Optional player reference
    player_name: Mapped[str | None] = mapped_column(String(100))

    # Event classification
    event_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), default="medium")

    # Confidence (0.0–1.0) — human-assigned
    confidence: Mapped[float] = mapped_column(Float, default=0.75, nullable=False)

    # Source attribution
    source_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(500))

    # Human-readable note
    note: Mapped[str] = mapped_column(String(1000), default="")

    # Who created this event
    created_by: Mapped[str] = mapped_column(String(50), default="admin")

    # Expiry — when this event should be disregarded
    expires_at: Mapped[str | None] = mapped_column(String(30))

    # Status
    status: Mapped[str] = mapped_column(String(20), default="active")


# ── Event type config (for CLI help and validation) ──

EVENT_TYPE_CONFIG = {
    "INJURY": {
        "description": "球员伤病，影响出场或状态",
        "xG_impact": -0.15,  # reduces team xG by ~15%
        "severities": ["low", "medium", "high", "critical"],
    },
    "SUSPENSION": {
        "description": "球员停赛，确定缺席",
        "xG_impact": -0.10,
        "severities": ["medium", "high", "critical"],
    },
    "LINEUP_CONFIRMED": {
        "description": "官方首发确认",
        "xG_impact": 0.0,  # informational, doesn't modify xG directly
        "severities": ["low"],
    },
    "LINEUP_RUMOR": {
        "description": "首发阵容传闻/媒体预测",
        "xG_impact": 0.0,
        "severities": ["low", "medium"],
    },
    "ROTATION_HINT": {
        "description": "轮换信号（教练表态/赛程密集）",
        "xG_impact": -0.05,
        "severities": ["low", "medium"],
    },
    "MOTIVATION": {
        "description": "手动覆盖/补充动机因素",
        "xG_impact": 0.05,
        "severities": ["low", "medium", "high"],
    },
    "WEATHER": {
        "description": "天气影响（手动补充 Open-Meteo 之外的天气情报）",
        "xG_impact": 0.0,
        "severities": ["low", "medium"],
    },
}
