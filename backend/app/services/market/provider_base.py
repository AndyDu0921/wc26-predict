"""Abstract base class for market odds providers.

All odds providers (API-Football, The Odds API, Football-Data.co.uk, etc.)
must implement this interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class MarketOddsResult:
    """Normalized result from any odds provider."""

    provider: str
    home_odds: float
    draw_odds: float
    away_odds: float
    implied_home: float
    implied_draw: float
    implied_away: float
    overround: float
    bookmaker: str = ""
    external_fixture_id: str = ""
    fetched_at: str = ""
    is_closing: bool = False
    raw_payload: dict[str, Any] | None = None


class MarketProvider(ABC):
    """Abstract base class for market odds providers.

    Each provider implements:
    - fetch(): Get current odds for a match
    - name: Provider identifier string
    - is_available(): Check if the provider is ready to use
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider name (e.g., 'api-football', 'the-odds-api')."""
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if this provider is configured and reachable."""
        ...

    @abstractmethod
    async def fetch(
        self,
        home_team: str,
        away_team: str,
        competition: str | None = None,
    ) -> MarketOddsResult | None:
        """Fetch current 1X2 odds for a match.

        Returns None if odds are unavailable (API down, no match found, etc.).
        """
        ...

    async def close(self) -> None:
        """Clean up resources (HTTP clients, connections)."""
        pass
