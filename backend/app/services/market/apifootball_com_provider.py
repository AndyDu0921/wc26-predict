"""apifootball.com odds provider — https://apiv3.apifootball.com/

Authentication: APIkey query parameter (NOT x-apisports-key header).
This is a DIFFERENT service from API-Sports / api-football.com.

Provider is designed to fail gracefully — if the key is invalid, rate-limited,
or odds are unavailable, fetch() returns None and the main prediction pipeline
continues unaffected.

Proxy support: auto-detects system proxy (Windows registry, env vars) and
routes all requests through it. Required for users behind firewalls/VPNs.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

import httpx

from app.config import get_settings
from app.services.market.probability import normalize_1x2_odds
from app.services.market.provider_base import MarketOddsResult, MarketProvider

logger = logging.getLogger(__name__)

# Known league IDs on apifootball.com (discovered via get_events, documented here)
# These differ from API-Sports league IDs.
KNOWN_LEAGUE_IDS: dict[str, int] = {
    "world cup": 28,           # World Cup - World Championship (country_id=8)
    "premier league": 2,
    "la liga": 3,
    "bundesliga": 4,
    "serie a": 5,
    "ligue 1": 6,
    "champions league": 7,
    "europa league": 8,
}


class ApifootballComProvider(MarketProvider):
    """apifootball.com v3 odds provider.

    Fetches pre-match 1X2 odds for a given match via team name matching.
    Falls back gracefully when the API is unavailable or odds are missing.

    Proxy: auto-detects from Windows registry / env vars. On first request,
    tests connectivity and caches the working transport mode (direct or proxy).
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key: str | None = settings.apifootball_com_key
        self.base_url = "https://apiv3.apifootball.com/"
        self._client: httpx.AsyncClient | None = None
        self._available: bool | None = None
        self._available_checked_at: float = 0.0  # monotonic timestamp of last check
        self._odds_available: bool | None = None
        self._odds_available_checked_at: float = 0.0
        self._proxy: str | None = self._detect_proxy()
        self._use_proxy: bool | None = None  # None = not tested yet
        if self._proxy:
            logger.debug("apifootball.com: proxy candidate %s", self._proxy)

    @staticmethod
    def _detect_proxy() -> str | None:
        """Auto-detect system proxy from env vars or Windows registry.

        Priority:
        1. HTTPS_PROXY / HTTP_PROXY / ALL_PROXY env vars
        2. Windows registry (HKCU Internet Settings)
        """
        # 1. environment variables
        for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
            val = os.environ.get(var, "").strip()
            if val:
                return val

        # 2. Windows registry
        if sys.platform == "win32":
            try:
                result = subprocess.run(
                    [
                        "reg", "query",
                        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                    ],
                    capture_output=True, text=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW
                    if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                )
                proxy_server = None
                for line in result.stdout.splitlines():
                    if "ProxyServer" in line:
                        parts = line.strip().split()
                        proxy_server = parts[-1] if len(parts) >= 3 else None
                if proxy_server and proxy_server.strip():
                    addr = proxy_server.strip()
                    return f"http://{addr}" if "://" not in addr else addr
            except Exception:
                pass

        return None

    async def _resolve_transport(self) -> httpx.AsyncClient:
        """Create a client, testing proxy vs direct to pick the working mode.

        On first call, attempts a direct connection. If it fails and a proxy
        candidate exists, falls back to the proxy. The result is cached for
        the provider's lifetime.
        """
        if self._client is not None:
            return self._client

        # Try direct first
        client = httpx.AsyncClient(timeout=15.0)
        if await self._try_connect(client):
            self._use_proxy = False
            self._client = client
            logger.debug("apifootball.com: using direct connection")
            return self._client

        # Direct failed — close and try proxy
        await client.aclose()

        if self._proxy:
            client = httpx.AsyncClient(timeout=15.0, proxy=self._proxy)
            if await self._try_connect(client):
                self._use_proxy = True
                self._client = client
                logger.info("apifootball.com: using proxy %s", self._proxy)
                return self._client
            await client.aclose()

        # Both failed — use direct client anyway (will fail gracefully later)
        logger.warning(
            "apifootball.com: connectivity check failed (direct + proxy both unreachable)"
        )
        self._client = httpx.AsyncClient(timeout=15.0)
        self._use_proxy = False
        return self._client

    async def _try_connect(self, client: httpx.AsyncClient) -> bool:
        """Quick connectivity check against the base API."""
        try:
            r = await client.get(
                self.base_url,
                params={**self._auth_params(), "action": "get_countries"},
            )
            data = r.json()
            return isinstance(data, list) and len(data) > 0
        except Exception:
            return False

    @property
    def name(self) -> str:
        return "apifootball.com"

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client with automatic transport resolution."""
        return await self._resolve_transport()

    def _auth_params(self) -> dict[str, str]:
        """Build query params with API key. Never logs the full key."""
        return {"APIkey": self.api_key or ""}

    # ── availability checks ─────────────────────────────────

    async def is_available(self) -> bool:
        """Check if basic API access works via get_countries.

        Re-checks after 120 s cooldown when previously unavailable (transient
        failures like rate limits or network blips should not permanently
        disable the provider).
        """
        import time as _time
        if self._available is not None:
            if self._available or _time.monotonic() - self._available_checked_at < 120:
                return self._available
            # Cooldown expired — re-check
            logger.info("apifootball.com: retrying availability check after cooldown")
        if not self.api_key:
            logger.warning("apifootball.com: no API key configured")
            self._available = False
            self._available_checked_at = _time.monotonic()
            return False
        try:
            client = await self._get_client()
            params = {**self._auth_params(), "action": "get_countries"}
            r = await client.get(self.base_url, params=params)
            data = r.json()
            if isinstance(data, dict) and "error" in data:
                logger.warning(f"apifootball.com auth error: {data['error']}")
                self._available = False
            elif isinstance(data, list) and len(data) > 0:
                self._available = True
                logger.info(f"apifootball.com: base API OK, {len(data)} countries")
            else:
                logger.warning("apifootball.com: unexpected response from get_countries")
                self._available = False
        except Exception as e:
            logger.warning(f"apifootball.com unavailable: {e}")
            self._available = False
        self._available_checked_at = _time.monotonic()
        return self._available

    async def is_odds_available(self) -> bool:
        """Check if odds endpoint returns data for current date range.

        Returns False does NOT mean the key is invalid — it may just mean
        no odds exist for today's date or the subscription plan doesn't cover odds.
        Re-checks after 120 s cooldown on failure.
        """
        import time as _time
        if self._odds_available is not None:
            if self._odds_available or _time.monotonic() - self._odds_available_checked_at < 120:
                return self._odds_available
            logger.info("apifootball.com: retrying odds availability check after cooldown")
        if not await self.is_available():
            self._odds_available = False
            self._odds_available_checked_at = _time.monotonic()
            return False
        try:
            client = await self._get_client()
            today_str = date.today().isoformat()
            params = {
                **self._auth_params(),
                "action": "get_odds",
                "from": today_str,
                "to": today_str,
            }
            r = await client.get(self.base_url, params=params)
            data = r.json()
            if isinstance(data, dict) and "error" in data:
                logger.warning(f"apifootball.com odds error: {data['error']}")
                self._odds_available = False
            elif isinstance(data, list):
                # Check if any entry has 1X2 odds fields
                has_odds = any(
                    all(k in entry for k in ("odd_1", "odd_x", "odd_2"))
                    for entry in data
                )
                if has_odds:
                    self._odds_available = True
                    logger.info("apifootball.com: odds endpoint available")
                else:
                    logger.info(
                        f"apifootball.com: odds endpoint reachable but no 1X2 odds "
                        f"for {today_str} (may be plan-limited)"
                    )
                    self._odds_available = False
            else:
                self._odds_available = False
        except Exception as e:
            logger.warning(f"apifootball.com odds check error: {e}")
            self._odds_available = False
        self._odds_available_checked_at = _time.monotonic()
        return self._odds_available

    # ── fetch ───────────────────────────────────────────────

    async def fetch(
        self,
        home_team: str,
        away_team: str,
        competition: str | None = None,
        kickoff_at: datetime | None = None,
    ) -> MarketOddsResult | None:
        """Fetch 1X2 odds from apifootball.com.

        Strategy:
        1. Determine date range (kickoff window or today + 7 days)
        2. Search events by date range, fuzzy-match team names
        3. Fetch odds for the matched fixture
        4. Normalize via vig removal
        """
        if not await self.is_available():
            return None

        # Determine date range
        if kickoff_at is not None:
            from_date = (kickoff_at - timedelta(days=1)).strftime("%Y-%m-%d")
            to_date = (kickoff_at + timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            from_date = date.today().isoformat()
            to_date = (date.today() + timedelta(days=7)).isoformat()

        try:
            client = await self._get_client()

            # ── Step 1: search events ──
            params: dict[str, str] = {
                **self._auth_params(),
                "action": "get_events",
                "from": from_date,
                "to": to_date,
            }
            # If we have a league mapping, use it to narrow results
            league_id = self._resolve_league_id(competition)
            if league_id is not None:
                params["league_id"] = str(league_id)

            r = await client.get(self.base_url, params=params)
            data = r.json()

            if isinstance(data, dict) and "error" in data:
                logger.warning(f"apifootball.com events error: {data['error']}")
                return None

            events = data if isinstance(data, list) else []
            if not events:
                logger.info(
                    f"apifootball.com: no events found for {from_date} → {to_date}"
                )
                return None

            # ── Step 2: fuzzy match by team name ──
            match_entry = None
            home_lower = self._normalize(home_team)
            away_lower = self._normalize(away_team)
            for ev in events:
                ev_home = self._normalize(str(ev.get("match_hometeam_name", "")))
                ev_away = self._normalize(str(ev.get("match_awayteam_name", "")))
                if (home_lower in ev_home or ev_home in home_lower) and \
                   (away_lower in ev_away or ev_away in away_lower):
                    match_entry = ev
                    break

            if match_entry is None:
                logger.info(
                    f"apifootball.com: no match found for {home_team} vs {away_team}"
                )
                return None

            match_id = str(match_entry.get("match_id", ""))
            match_date_raw = match_entry.get("match_date", from_date)

            # ── Step 3: fetch odds ──
            odds_params = {
                **self._auth_params(),
                "action": "get_odds",
                "from": match_date_raw,
                "to": match_date_raw,
                "match_id": match_id,
            }
            r = await client.get(self.base_url, params=odds_params)
            odds_data = r.json()

            if isinstance(odds_data, dict) and "error" in odds_data:
                logger.warning(f"apifootball.com odds error: {odds_data['error']}")
                return None

            odds_list = odds_data if isinstance(odds_data, list) else []
            if not odds_list:
                logger.info(
                    f"apifootball.com: no odds for match_id={match_id} "
                    f"({home_team} vs {away_team})"
                )
                return None

            # ── Step 4: extract 1X2 ──
            return self._extract_1x2(odds_list, match_id)

        except Exception as e:
            logger.warning(f"apifootball.com fetch error: {e}")
            return None

    # ── internal helpers ────────────────────────────────────

    def _extract_1x2(
        self, odds_list: list[dict], match_id: str
    ) -> MarketOddsResult | None:
        """Extract 1X2 odds from the first entry with odd_1/odd_x/odd_2."""
        for entry in odds_list:
            odd_1 = entry.get("odd_1")
            odd_x = entry.get("odd_x")
            odd_2 = entry.get("odd_2")
            if not all([odd_1, odd_x, odd_2]):
                continue
            try:
                home_odds = float(odd_1)
                draw_odds = float(odd_x)
                away_odds = float(odd_2)
            except (ValueError, TypeError):
                continue

            if any(o <= 1.0 for o in (home_odds, draw_odds, away_odds)):
                continue

            # Vig removal
            norm = normalize_1x2_odds(home_odds, draw_odds, away_odds)

            return MarketOddsResult(
                provider=self.name,
                home_odds=home_odds,
                draw_odds=draw_odds,
                away_odds=away_odds,
                implied_home=norm["home"],
                implied_draw=norm["draw"],
                implied_away=norm["away"],
                overround=norm["overround"],
                bookmaker=entry.get("odd_bookmakers", "apifootball.com"),
                external_fixture_id=match_id,
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )

        logger.info("apifootball.com: no valid 1X2 odds in response")
        return None

    def _resolve_league_id(self, competition: str | None) -> int | None:
        """Map competition name to apifootball.com league ID."""
        if not competition:
            return None
        c = competition.lower()
        for key, lid in KNOWN_LEAGUE_IDS.items():
            if key in c:
                return lid
        return None

    @staticmethod
    def _normalize(name: str) -> str:
        """Normalize team name for fuzzy matching."""
        return name.lower().replace("&", "and").strip()

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._available = None
        self._odds_available = None
        self._use_proxy = None
