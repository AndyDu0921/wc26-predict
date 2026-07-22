"""Market consensus schemas — dataclasses for odds snapshots and consensus."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OddsSnapshot:
    """Raw odds data from a single provider for a single match at a point in time."""

    match_id: str
    provider: str
    captured_at: str  # ISO format
    home_odds: float
    draw_odds: float
    away_odds: float
    implied_home: float = 0.0
    implied_draw: float = 0.0
    implied_away: float = 0.0
    overround: float = 0.0
    bookmaker: str = ""
    external_fixture_id: str = ""
    kickoff_at: str = ""
    is_closing: bool = False
    source_payload_json: str = ""
    id: str = ""

    def __post_init__(self):
        if not self.id:
            import uuid
            self.id = uuid.uuid4().hex


@dataclass
class MarketConsensus:
    """Aggregated market consensus from one or more providers."""

    match_id: str
    captured_at: str  # ISO format
    consensus_home: float
    consensus_draw: float
    consensus_away: float
    bookmaker_count: int = 1
    provider_count: int = 1
    overround_avg: float = 0.0
    confidence: float = 0.5  # 0.0-1.0, based on provider count and agreement
    kickoff_at: str = ""
    source_snapshot_ids: list[str] = field(default_factory=list)
    id: str = ""

    def __post_init__(self):
        if not self.id:
            import uuid
            self.id = uuid.uuid4().hex

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "match_id": self.match_id,
            "captured_at": self.captured_at,
            "kickoff_at": self.kickoff_at,
            "consensus_home": self.consensus_home,
            "consensus_draw": self.consensus_draw,
            "consensus_away": self.consensus_away,
            "bookmaker_count": self.bookmaker_count,
            "provider_count": self.provider_count,
            "overround_avg": self.overround_avg,
            "confidence": self.confidence,
            "source_snapshot_ids": self.source_snapshot_ids,
        }
