"""sync_provider.py — Synchronous market odds wrapper for Dashboard.

Wraps the async apifootball.com provider (and The Odds API fallback) in
sync functions via asyncio.run(). Designed for Streamlit which runs in a
synchronous context.

Provides:
    fetch_market_consensus_sync(home_team, away_team, competition)
        -> dict | None

Graceful degradation:
    1. apifootball.com       (primary — live API)
    2. The Odds API          (secondary — live API)
    3. _web_odds_cache.json   (tertiary — web-search multi-bookmaker consensus)
    4. _manual_odds.json       (last resort — single-bookmaker cached odds)
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Path to manual odds cache — populated via web search when API is exhausted.
# Format: {"Switzerland|Canada": {"home_odds": 2.45, "draw_odds": 3.10, ...}}
_MANUAL_ODDS_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "_manual_odds.json"


def _lookup_web_consensus(home_team: str, away_team: str) -> dict[str, Any] | None:
    """Check the web odds cache for multi-bookmaker consensus.

    This is the tertiary fallback when live APIs return 0 or ≤2 bookmakers.
    The cache is populated via web search (Claude → ingest_web_odds.py →
    _web_odds_cache.json) with 8-12 bookmakers for high-profile matches.

    Returns None when the cache entry is stale (>6h) or missing.
    """
    try:
        from app.services.market.web_odds_aggregator import lookup
        return lookup(home_team, away_team)
    except Exception as exc:
        logger.debug("Web odds aggregator unavailable: %s", exc)
        return None


def _lookup_manual_odds(home_team: str, away_team: str) -> dict[str, Any] | None:
    """Check the manual odds file for web-verified odds.

    Used as a quaternary fallback when apifootball.com, The Odds API, and
    the web odds cache are all unavailable or have exhausted their quotas.
    """
    if not _MANUAL_ODDS_PATH.exists():
        return None
    try:
        manual = json.loads(_MANUAL_ODDS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None

    key = f"{home_team}|{away_team}"
    entry = manual.get(key)
    if not entry:
        return None

    home_odds = float(entry.get("home_odds", 0))
    draw_odds = float(entry.get("draw_odds", 0))
    away_odds = float(entry.get("away_odds", 0))
    if not (home_odds > 1 and draw_odds > 1 and away_odds > 1):
        return None

    raw_h = 1 / home_odds
    raw_d = 1 / draw_odds
    raw_a = 1 / away_odds
    total = raw_h + raw_d + raw_a

    logger.info(
        "Market odds from manual file: H=%.4f D=%.4f A=%.4f (%s)",
        raw_h / total, raw_d / total, raw_a / total, entry.get("source", "manual"),
    )
    return {
        "home_prob": raw_h / total,
        "draw_prob": raw_d / total,
        "away_prob": raw_a / total,
        "provider": entry.get("source", "manual-odds"),
        "overround": total - 1.0,
        "home_odds": home_odds,
        "draw_odds": draw_odds,
        "away_odds": away_odds,
        "bookmaker": entry.get("source", "web-verified"),
        "manual_entry": True,
    }


def fetch_market_consensus_sync(
    home_team: str,
    away_team: str,
    competition: str | None = None,
    timeout: float = 8.0,
) -> dict[str, Any] | None:
    """Fetch 1X2 market implied probabilities synchronously.

    Priority (V4.3.2 — web consensus as tertiary fallback):
    1. apifootball.com (live API — key configured)
    2. The Odds API (live API — key configured)
    3. _web_odds_cache.json (web-search multi-bookmaker consensus, 6h TTL)
    4. _manual_odds.json (last resort — single-bookmaker cached odds)

    Returns:
        {
            "home_prob": 0.52,
            "draw_prob": 0.24,
            "away_prob": 0.24,
            "provider": "apifootball.com",
            "overround": 0.05,
            "home_odds": 1.85,
            "draw_odds": 3.50,
            "away_odds": 4.00,
        }
        or None if all providers failed.
    """
    # ── Primary: live APIs (apifootball.com → The Odds API) ──
    # Both APIs are operational as of V4.3.2. Try them first for fresh odds.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:
        result = asyncio.run(
            _fetch_consensus_async(home_team, away_team, competition, timeout)
        )
        if result is not None:
            return result
    else:
        logger.debug(
            "fetch_market_consensus_sync: running event loop detected — "
            "skipping async API calls for %s vs %s",
            home_team, away_team,
        )

    # ── Fallback: web odds cache (multi-bookmaker consensus) ──
    # Pure file I/O — works in both sync and async contexts.
    result = _lookup_web_consensus(home_team, away_team)
    if result is not None:
        logger.info(
            "Market odds from web consensus (%d bookmakers): %s vs %s",
            result.get("sample_bookmakers", 0), home_team, away_team,
        )
        return result

    # ── Fallback: manual odds file (last resort) ──
    # Pure file I/O — works in both sync and async contexts.
    result = _lookup_manual_odds(home_team, away_team)
    if result is not None:
        logger.info(
            "Market odds from manual file (API fallback): %s vs %s",
            home_team, away_team,
        )
        return result

    return None


async def _fetch_consensus_async(
    home_team: str,
    away_team: str,
    competition: str | None,
    timeout: float,
) -> dict[str, Any] | None:
    """Async implementation — tries providers sequentially."""
    result = None

    # Provider 1: apifootball.com
    try:
        from app.services.market.apifootball_com_provider import (
            ApifootballComProvider,
        )

        provider = ApifootballComProvider()
        if await asyncio.wait_for(provider.is_available(), timeout=timeout):
            odds = await asyncio.wait_for(
                provider.fetch(home_team, away_team, competition),
                timeout=timeout,
            )
            if odds is not None:
                result = {
                    "home_prob": odds.implied_home,
                    "draw_prob": odds.implied_draw,
                    "away_prob": odds.implied_away,
                    "provider": odds.provider,
                    "overround": odds.overround,
                    "home_odds": odds.home_odds,
                    "draw_odds": odds.draw_odds,
                    "away_odds": odds.away_odds,
                    "bookmaker": odds.bookmaker,
                }
                logger.info(
                    f"Market odds from {odds.provider}: "
                    f"H={odds.implied_home:.3f} D={odds.implied_draw:.3f} "
                    f"A={odds.implied_away:.3f}"
                )
        await provider.close()
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning(f"apifootball.com sync fetch failed: {e}")

    if result is not None:
        return result

    # Provider 2: The Odds API
    try:
        result = await _fetch_theodds_api(home_team, away_team, competition, timeout)
    except Exception as e:
        logger.warning(f"The Odds API sync fetch failed: {e}")

    return result


async def _fetch_theodds_api(
    home_team: str,
    away_team: str,
    competition: str | None,
    timeout: float,
) -> dict[str, Any] | None:
    """Fetch from The Odds API (sports odds aggregator)."""
    import httpx

    from app.config import get_settings

    settings = get_settings()
    api_key = settings.odds_api_key
    if not api_key:
        logger.info("The Odds API: no key configured")
        return None

    base_url = "https://api.the-odds-api.com/v4"
    # The Odds API uses 'soccer' for all football
    sport = "soccer"

    # Determine region and sport key hints
    region = "eu"  # default
    is_wc = False
    if competition:
        comp_lower = competition.lower()
        if "world cup" in comp_lower or "fifa" in comp_lower:
            region = "us,uk,eu"  # WC: 三区域全覆盖，避免US-only遗漏低关注度比赛
            is_wc = True

    # Prefer WC-specific sport key when available — the generic 'soccer' feed
    # often omits lower-profile WC group-stage matches (observed 2026-06-22:
    # Norway-Senegal & Jordan-Algeria missing from 'soccer' but present under
    # 'soccer_fifa_world_cup' with 48 bookmakers each).
    sport_keys_to_try = ["soccer_fifa_world_cup", "soccer"] if is_wc else ["soccer"]

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for sport_idx, sport in enumerate(sport_keys_to_try):
                url = f"{base_url}/sports/{sport}/odds"
                params = {
                    "apiKey": api_key,
                    "regions": region,
                    "markets": "h2h",
                    "oddsFormat": "decimal",
                }
                resp = await client.get(url, params=params)
                if resp.status_code != 200:
                    logger.warning(
                        f"The Odds API {sport} returned {resp.status_code}: {resp.text[:200]}"
                    )
                    continue

                data = resp.json()
                if not isinstance(data, list) or len(data) == 0:
                    continue

                # Search for our match
                home_lower = home_team.lower().strip()
                away_lower = away_team.lower().strip()
                match_found = False
                for event in data:
                    ev_home = event.get("home_team", "").lower().strip()
                    ev_away = event.get("away_team", "").lower().strip()
                    if (home_lower in ev_home or ev_home in home_lower) and (
                        away_lower in ev_away or ev_away in away_lower
                    ):
                        # Found match — aggregate every valid 1X2 bookmaker.
                        bookmakers = event.get("bookmakers", [])
                        if not bookmakers:
                            continue
                        from app.services.market.bookmaker_consensus import (
                            consensus_from_bookmakers,
                        )

                        consensus = consensus_from_bookmakers(
                            event,
                            home_team,
                            away_team,
                            provider="the-odds-api",
                            min_bookmakers=1,
                        )
                        if consensus:
                            consensus["sport_key"] = sport
                            logger.info(
                                "Market odds from The Odds API (%d bookmakers, sport=%s): "
                                "H=%.3f D=%.3f A=%.3f",
                                consensus.get("sample_bookmakers", 0),
                                sport,
                                consensus["home_prob"],
                                consensus["draw_prob"],
                                consensus["away_prob"],
                            )
                            return consensus
                        match_found = True  # Found the event but couldn't extract odds

                if match_found:
                    logger.info(
                        f"The Odds API: found {home_team} vs {away_team} "
                        f"in {sport} but couldn't extract valid 1X2 odds"
                    )

            # Exhausted all sport keys
            logger.info(
                f"The Odds API: no match found for {home_team} vs {away_team}"
            )
            return None

    except Exception as e:
        logger.warning(f"The Odds API error: {e}")
        return None
