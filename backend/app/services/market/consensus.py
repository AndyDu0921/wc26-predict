"""Market consensus builder — aggregates odds from multiple providers.

When multiple providers report odds for the same match:
  1. Normalize each provider's odds (vig removal).
  2. Weight by provider reliability (bookmaker count, historical accuracy).
  3. Average implied probabilities.
  4. Compute confidence score.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.market.schemas import MarketConsensus, OddsSnapshot


def build_consensus(
    snapshots: list[OddsSnapshot],
    match_id: str = "",
    kickoff_at: str = "",
) -> MarketConsensus | None:
    """Build a market consensus from one or more provider snapshots.

    Args:
        snapshots: List of OddsSnapshot objects (may be from different providers).
        match_id: Match identifier.
        kickoff_at: Match kickoff time (ISO format).

    Returns:
        MarketConsensus or None if no valid snapshots.
    """
    if not snapshots:
        return None

    valid = [s for s in snapshots if s.implied_home > 0 and s.implied_draw > 0 and s.implied_away > 0]
    if not valid:
        return None

    # Simple equally-weighted average across providers
    n = len(valid)
    consensus_home = sum(s.implied_home for s in valid) / n
    consensus_draw = sum(s.implied_draw for s in valid) / n
    consensus_away = sum(s.implied_away for s in valid) / n

    # Renormalize
    total = consensus_home + consensus_draw + consensus_away
    if total > 0:
        consensus_home /= total
        consensus_draw /= total
        consensus_away /= total

    # Count unique providers and bookmakers
    providers = set(s.provider for s in valid)
    total_bookmakers = sum(s.bookmaker_count if hasattr(s, 'bookmaker_count') else 1 for s in valid if s.bookmaker_count)

    # Confidence: higher with more providers and bookmakers
    provider_confidence = min(1.0, len(providers) / 4.0)  # 4+ providers = full confidence
    bookmaker_confidence = min(1.0, total_bookmakers / 8.0)  # 8+ bookmakers = full confidence
    # Agreement: lower std = higher confidence
    homes = [s.implied_home for s in valid]
    if len(homes) > 1:
        import statistics
        try:
            std = statistics.stdev(homes)
            agreement = max(0.0, 1.0 - std * 10.0)  # std=0.1 → agreement=0.0
        except statistics.StatisticsError:
            agreement = 0.0
    else:
        agreement = 0.0

    confidence = round(0.3 * provider_confidence + 0.3 * bookmaker_confidence + 0.4 * agreement, 4)

    return MarketConsensus(
        match_id=match_id,
        captured_at=datetime.now(timezone.utc).isoformat(),
        kickoff_at=kickoff_at,
        consensus_home=round(consensus_home, 6),
        consensus_draw=round(consensus_draw, 6),
        consensus_away=round(consensus_away, 6),
        bookmaker_count=total_bookmakers,
        provider_count=len(providers),
        overround_avg=round(sum(s.overround for s in valid) / n, 6),
        confidence=confidence,
        source_snapshot_ids=[s.id for s in valid],
    )


def snapshots_from_market_probs(
    market_probs: dict[str, Any],
    match_id: str,
    provider: str = "the-odds-api",
    kickoff_at: str = "",
) -> OddsSnapshot | None:
    """Convert a market_probs dict (from MarketCalibrator) to an OddsSnapshot.

    The market_probs dict format:
      {home_prob, draw_prob, away_prob, vig, sample_bookmakers, sport_key, fetched_at}
    """
    if not market_probs:
        return None

    return OddsSnapshot(
        match_id=match_id,
        provider=provider,
        captured_at=market_probs.get("fetched_at", datetime.now(timezone.utc).isoformat()),
        home_odds=0.0,  # Not stored in market_probs (only implied probs)
        draw_odds=0.0,
        away_odds=0.0,
        implied_home=float(market_probs.get("home_prob", 0)),
        implied_draw=float(market_probs.get("draw_prob", 0)),
        implied_away=float(market_probs.get("away_prob", 0)),
        overround=float(market_probs.get("vig", 0)),
        bookmaker=str(market_probs.get("bookmaker", "Pinnacle")),
        kickoff_at=kickoff_at,
    )
