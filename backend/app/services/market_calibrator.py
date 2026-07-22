"""MarketCalibrator — extract signals from betting markets without blending.

Design principles (confirmed with user 2026-05-15):
1. Phase 1: divergence detection ONLY — no probability modification
2. Only store implied probabilities (after vig removal), never raw odds
3. Max 25% market blend reserved for future, not active now
4. Divergence > 12pp → risk_tags + confidence_penalty + logging
5. Never display odds numbers to users
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.services.market.provider_base import MarketProvider

logger = logging.getLogger(__name__)

# Only fetch odds for high-weight competitions (World Cup, UCL, Top 5 leagues)
HIGH_WEIGHT_THRESHOLD = 0.82
DIVERGENCE_THRESHOLD = 0.12   # 12pp divergence triggers risk tag
MAX_MARKET_BLEND = 0.25       # market contributes at most 25%
MIN_MARKET_BLEND = 0.05       # always blend at least 5% when market data available
BLEND_SATURATION = 2000       # sample_size at which market weight reaches minimum


class MarketCalibrator:
    """Fetch market consensus and detect model-market divergence.

    Supports multiple provider backends:
    - The Odds API (ODDS_API_KEY) — sports odds aggregator
    - apifootball.com (APIFOOTBALL_COM_KEY) — match-level odds

    Provider selection (unified with sync_provider.py):
    apifootball.com → The Odds API.
    """

    def __init__(self, shadow_mode: bool = True) -> None:
        settings = get_settings()
        self.api_key: str | None = settings.odds_api_key
        self.base_url = "https://api.the-odds-api.com/v4"
        self._client: httpx.AsyncClient | None = None
        self._available: bool | None = None
        self.shadow_mode: bool = shadow_mode  # Default: record only, don't blend

        # Odds-specific providers (separate from The Odds API)
        self._odds_provider: MarketProvider | None = None
        self._odds_provider_resolved: bool = False
        self._apifootball_key: str | None = settings.apifootball_com_key

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def is_available(self) -> bool:
        """Check if the odds API is configured and reachable.

        Always re-checks if previously unavailable — transient errors
        (network blips, DNS, timeouts) should not permanently disable odds.
        """
        if self._available is not None and self._available:
            return True
        if not self.api_key:
            logger.info("ODDS_API_KEY not configured — market calibrator disabled")
            self._available = False
            return False
        try:
            client = await self._get_client()
            resp = await client.get(
                f"{self.base_url}/sports",
                params={"apiKey": self.api_key},
            )
            self._available = resp.status_code == 200
            if not self._available:
                logger.warning(f"Odds API returned {resp.status_code}")
        except Exception as e:
            logger.warning(f"Odds API unreachable: {e}")
            self._available = False
        return self._available

    async def _resolve_odds_provider(self) -> MarketProvider | None:
        """Resolve the best available odds-specific provider from configured keys.

        Priority: apifootball.com (if odds available).
        The Odds API is handled separately in fetch_market_probs() as a
        secondary fallback after the odds provider is exhausted.

        Returns None if no provider is configured or no provider has odds available.
        Logs the reason for transparency.
        """
        if self._odds_provider_resolved:
            return self._odds_provider

        # ── Try apifootball.com ──
        if self._apifootball_key:
            from app.services.market.apifootball_com_provider import \
                ApifootballComProvider
            provider = ApifootballComProvider()
            try:
                if await provider.is_available():
                    if await provider.is_odds_available():
                        self._odds_provider = provider
                        self._odds_provider_resolved = True
                        logger.info("market provider: apifootball.com (odds available)")
                        return self._odds_provider
                    else:
                        logger.info(
                            "market provider: apifootball.com base API OK but "
                            "odds unavailable — provider_no_odds"
                        )
                        await provider.close()
                else:
                    logger.info(
                        "market provider: apifootball.com unavailable — "
                        "no_provider"
                    )
                    await provider.close()
            except Exception as e:
                logger.warning(f"market provider: apifootball.com error: {e}")
                try:
                    await provider.close()
                except Exception as exc:
                    logger.debug("Best-effort provider close failed: %s", exc)

        self._odds_provider = None
        self._odds_provider_resolved = True
        logger.info("market provider: none available — no_provider")
        return None

    async def fetch_market_probs(
        self,
        home_team: str,
        away_team: str,
        competition_weight: float = 0.9,
        competition: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch market-implied probabilities for a match.

        Provider priority (V4.3.2 — unified with sync_provider.py):
        1. apifootball.com (if key configured and odds available)
        2. The Odds API (secondary fallback)
        3. Web search multi-bookmaker consensus (_web_odds_cache.json)
        4. Manual odds file (_manual_odds.json, last resort)

        Returns None if:
        - No API keys are configured
        - Competition weight is below threshold
        - All providers fail or no odds available for this match
        """
        if competition_weight < HIGH_WEIGHT_THRESHOLD:
            return None

        has_odds_api = self.api_key and (self._available is None or self._available)

        # ── Provider 1+2: apifootball.com → API-Sports ──
        try:
            odds_prov = await self._resolve_odds_provider()
            if odds_prov is not None:
                result = await odds_prov.fetch(home_team, away_team, competition)
                if result is not None:
                    from datetime import datetime as _dt, timezone as _tz
                    logger.info(
                        f"Market data from {odds_prov.name} for "
                        f"{home_team} vs {away_team}: "
                        f"home={result.implied_home:.3f}"
                    )
                    return {
                        "home_prob": result.implied_home,
                        "draw_prob": result.implied_draw,
                        "away_prob": result.implied_away,
                        "vig": result.overround,
                        "sample_bookmakers": 1,
                        "source": odds_prov.name,
                        "bookmaker": result.bookmaker,
                        "fetched_at": result.fetched_at,
                    }
        except Exception as e:
            logger.warning(f"market odds-specific provider error: {e}")

        # ── Provider 3: The Odds API (fallback) ──
        if not has_odds_api:
            if self.api_key:
                try:
                    has_odds_api = await self.is_available()
                except Exception:
                    has_odds_api = False
            if not has_odds_api:
                logger.info(
                    f"No market data for {home_team} vs {away_team} — "
                    f"all providers exhausted"
                )
                return None

        # Map competition to sport keys
        sport_keys = self._competition_sport_keys(competition)

        try:
            client = await self._get_client()

            for sport_key in sport_keys:
                resp = await client.get(
                    f"{self.base_url}/sports/{sport_key}/odds",
                    params={
                        "apiKey": self.api_key,
                        "regions": "eu",
                        "markets": "h2h",
                        "oddsFormat": "decimal",
                    },
                )
                if resp.status_code != 200:
                    continue

                data = resp.json()
                if not data:
                    continue

                # Find match by team names (fuzzy match)
                for match_data in data:
                    if self._teams_match(match_data, home_team, away_team):
                        result = self._extract_probs(match_data, home_team, away_team)
                        if result:
                            from datetime import datetime as _dt, timezone as _tz
                            result["sport_key"] = sport_key
                            result["fetched_at"] = _dt.now(_tz.utc).isoformat()
                            result["source"] = "the-odds-api"
                            logger.info(
                                f"Market data found for {home_team} vs {away_team} "
                                f"in {sport_key}: home={result['home_prob']:.3f}"
                            )
                            return result

            logger.info(
                f"No odds found for {home_team} vs {away_team} "
                f"in {sport_keys} (all providers exhausted)"
            )

        except Exception as e:
            logger.warning(f"Market fetch error (The Odds API): {e}")

        # ── Provider 3: Web search multi-bookmaker consensus ──
        # Pure file I/O — reads _web_odds_cache.json populated by ingest_web_odds.py
        try:
            from app.services.market.web_odds_aggregator import lookup
            web_result = lookup(home_team, away_team)
            if web_result is not None:
                from datetime import datetime as _dt, timezone as _tz
                logger.info(
                    f"Market data from web consensus "
                    f"({web_result.get('sample_bookmakers', 0)} bookmakers) for "
                    f"{home_team} vs {away_team}: "
                    f"home={web_result['home_prob']:.3f}"
                )
                return {
                    "home_prob": web_result["home_prob"],
                    "draw_prob": web_result["draw_prob"],
                    "away_prob": web_result["away_prob"],
                    "vig": web_result.get("overround", 0),
                    "sample_bookmakers": web_result.get("sample_bookmakers", 0),
                    "source": "web-search-consensus",
                    "bookmaker": web_result.get("bookmaker", "multi-bookmaker"),
                    "web_verified": True,
                    "cv": {
                        "home": web_result.get("cv_home", 0),
                        "draw": web_result.get("cv_draw", 0),
                        "away": web_result.get("cv_away", 0),
                    },
                    "fetched_at": web_result.get("captured_at", ""),
                }
        except Exception as e:
            logger.debug("Web odds aggregator lookup failed: %s", e)

        # ── Provider 4: Manual odds file (last resort) ──
        try:
            from app.services.market.sync_provider import _lookup_manual_odds
            manual_result = _lookup_manual_odds(home_team, away_team)
            if manual_result is not None:
                from datetime import datetime as _dt, timezone as _tz
                logger.info(
                    f"Market data from manual file for "
                    f"{home_team} vs {away_team}: "
                    f"home={manual_result['home_prob']:.3f}"
                )
                return {
                    "home_prob": manual_result["home_prob"],
                    "draw_prob": manual_result["draw_prob"],
                    "away_prob": manual_result["away_prob"],
                    "vig": manual_result.get("overround", 0),
                    "sample_bookmakers": 1,
                    "source": "manual-odds-file",
                    "bookmaker": manual_result.get("bookmaker", "manual"),
                    "fetched_at": "",
                }
        except Exception as e:
            logger.debug("Manual odds lookup failed: %s", e)

        return None

    @staticmethod
    def _competition_sport_keys(competition: str | None) -> list[str]:
        """Map competition name to The Odds API sport keys to try.

        Returns a list — first match wins. Falls back to EPL if unknown.
        """
        if not competition:
            return ["soccer_epl"]

        comp_lower = competition.lower()

        mapping = {
            "premier league": ["soccer_epl"],
            "primera division": ["soccer_spain_la_liga"],
            "la liga": ["soccer_spain_la_liga"],
            "bundesliga": ["soccer_germany_bundesliga"],
            "serie a": ["soccer_italy_serie_a"],
            "ligue 1": ["soccer_france_ligue_one"],
            "champions league": ["soccer_uefa_champs_league"],
            "europa league": ["soccer_uefa_europa_league"],
            "fa cup": ["soccer_fa_cup", "soccer_epl"],
            "world cup": ["soccer_fifa_world_cup"],
            "fifa world cup": ["soccer_fifa_world_cup"],
        }

        for key, sport_keys in mapping.items():
            if key in comp_lower:
                return sport_keys

        return ["soccer_epl"]  # fallback

    @staticmethod
    def _normalize_team(name: str) -> str:
        """Normalize team name for fuzzy matching.

        Handles: & ↔ and, FC/AFC suffix, extra whitespace, punctuation.
        """
        import re
        n = name.lower().strip()
        n = n.replace("&", "and")
        n = re.sub(r"\s+", " ", n)
        # Remove common affixes that differ between data sources
        for suffix in (" fc", " afc"):
            if n.endswith(suffix):
                n = n[:-len(suffix)]
                break
        for prefix in ("afc ",):
            if n.startswith(prefix):
                n = n[len(prefix):]
                break
        return n

    def _teams_match(self, match_data: dict, home: str, away: str) -> bool:
        """Fuzzy check if API match data matches our team names."""
        api_home = self._normalize_team(match_data.get("home_team", ""))
        api_away = self._normalize_team(match_data.get("away_team", ""))
        home_norm = self._normalize_team(home)
        away_norm = self._normalize_team(away)
        return (
            home_norm in api_home or api_home in home_norm
        ) and (
            away_norm in api_away or api_away in away_norm
        )

    def _extract_probs(self, match_data: dict, home_team: str, away_team: str) -> dict[str, Any] | None:
        """Extract vig-removed implied probabilities from odds data.

        Matches by team name, not outcome order (API does not guarantee
        home-first ordering).
        """
        bookmakers = match_data.get("bookmakers", [])
        if not bookmakers:
            return None

        from app.services.market.bookmaker_consensus import (
            consensus_from_bookmakers,
        )

        consensus = consensus_from_bookmakers(
            match_data,
            home_team,
            away_team,
            provider="the-odds-api",
            min_bookmakers=1,
        )
        if not consensus:
            return None
        return {
            "home_prob": consensus["home_prob"],
            "draw_prob": consensus["draw_prob"],
            "away_prob": consensus["away_prob"],
            "vig": consensus["overround"],
            "sample_bookmakers": consensus["sample_bookmakers"],
            "bookmaker": consensus["bookmaker"],
            "bookmaker_list": consensus["bookmaker_list"],
            "home_odds": consensus["home_odds"],
            "draw_odds": consensus["draw_odds"],
            "away_odds": consensus["away_odds"],
            "consensus_method": consensus["consensus_method"],
        }

    def _remove_vig(
        self, home_price: float, draw_price: float, away_price: float
    ) -> dict[str, Any]:
        """Convert raw odds to fair probabilities by removing bookmaker margin.

        Raw odds like 1.85/3.60/4.20 sum to >1.0 in implied prob space.
        We normalize to sum=1.0 and record the removed vig.
        """
        home_implied = 1.0 / home_price
        draw_implied = 1.0 / draw_price
        away_implied = 1.0 / away_price
        total = home_implied + draw_implied + away_implied

        return {
            "home_prob": home_implied / total,
            "draw_prob": draw_implied / total,
            "away_prob": away_implied / total,
            "vig": total - 1.0,
            "sample_bookmakers": 1,
        }

    def calibrate(
        self,
        model_probs: dict[str, float],
        market_probs: dict[str, Any] | None,
        sample_size: int = 0,
    ) -> dict[str, Any]:
        """Detect model-market divergence and blend probabilities.

        Phase 2 (full): blends market implied probabilities into model output
        with sample-size-aware weighting.

        Parameters
        ----------
        model_probs : dict with home_win_prob, draw_prob, away_win_prob
        market_probs : dict from fetch_market_probs() or None
        sample_size : training sample count (more samples → less market weight)

        Returns
        -------
        dict with blended probabilities + metadata
        """
        if market_probs is None:
            return {
                "home_win_prob": model_probs["home_win_prob"],
                "draw_prob": model_probs.get("draw_prob", 0.0),
                "away_win_prob": model_probs["away_win_prob"],
                "market_applied": False,
                "market_weight_used": 0.0,
                "divergence": None,
                "risk_tags": [],
                "confidence_penalty": 0.0,
                "market_home_prob": None,
            }

        # Divergence detection (always computed, even in shadow mode)
        model_home = model_probs.get("home_win_prob", 0.5)
        market_home = market_probs["home_prob"]
        divergence = abs(model_home - market_home)

        risk_tags = []
        confidence_penalty = 0.0

        if divergence > DIVERGENCE_THRESHOLD:
            risk_tags.append(
                f"模型与市场存在显著分歧 ({divergence*100:.1f}pp)"
            )
            confidence_penalty = min(divergence * 0.5, 0.15)

        # Compute blend weight (for audit tracking, even in shadow mode)
        market_weight = max(
            MIN_MARKET_BLEND,
            min(MAX_MARKET_BLEND, 0.25 - sample_size / BLEND_SATURATION),
        )

        # ── Shadow mode: return model probs unchanged ──
        if self.shadow_mode:
            return {
                "home_win_prob": model_probs["home_win_prob"],
                "draw_prob": model_probs.get("draw_prob", 0.0),
                "away_win_prob": model_probs["away_win_prob"],
                "market_applied": False,  # NOT applied — shadow mode
                "market_weight_used": market_weight,  # candidate weight (recorded)
                "divergence": divergence,
                "divergence_triggered": divergence > DIVERGENCE_THRESHOLD,
                "risk_tags": risk_tags,
                "confidence_penalty": confidence_penalty,
                "market_home_prob": market_probs["home_prob"],
                "market_draw_prob": market_probs["draw_prob"],
                "market_away_prob": market_probs["away_prob"],
                "market_vig": market_probs.get("vig", 0),
                "shadow_mode": True,
            }

        # ── Active mode: blend market into model ──
        model_weight = 1.0 - market_weight

        blended_home = (
            model_home * model_weight + market_probs["home_prob"] * market_weight
        )
        blended_draw = (
            model_probs.get("draw_prob", 0.0) * model_weight
            + market_probs["draw_prob"] * market_weight
        )
        blended_away = (
            model_probs.get("away_win_prob", 0.0) * model_weight
            + market_probs["away_prob"] * market_weight
        )

        # Normalize
        total = blended_home + blended_draw + blended_away
        if total > 0:
            blended_home /= total
            blended_draw /= total
            blended_away /= total

        return {
            "home_win_prob": blended_home,
            "draw_prob": blended_draw,
            "away_win_prob": blended_away,
            "market_applied": True,
            "market_weight_used": market_weight,
            "divergence": divergence,
            "divergence_triggered": divergence > DIVERGENCE_THRESHOLD,
            "risk_tags": risk_tags,
            "confidence_penalty": confidence_penalty,
            "market_home_prob": market_probs["home_prob"],
            "market_draw_prob": market_probs["draw_prob"],
            "market_away_prob": market_probs["away_prob"],
            "market_vig": market_probs.get("vig", 0),
            "shadow_mode": False,
        }

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None


# Singleton
_calibrator: MarketCalibrator | None = None


def get_calibrator(shadow_mode: bool = True) -> MarketCalibrator:
    """Get or create the MarketCalibrator singleton.

    Args:
        shadow_mode: If True (default), market data is recorded but NOT
            blended into predictions. Set to False only after backtest
            proves improvement over BaseOnly.
    """
    global _calibrator
    if _calibrator is None or _calibrator.shadow_mode != shadow_mode:
        _calibrator = MarketCalibrator(shadow_mode=shadow_mode)
    return _calibrator
