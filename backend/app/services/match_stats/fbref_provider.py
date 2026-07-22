"""FBref match statistics provider via soccerdata library.

Data available (per-match team logs):
  shooting: Gls, Sh, SoT, SoT%, G/Sh, G/SoT, PK, PKatt
  misc:     CrdY, CrdR, 2CrdY, Fls, Fld, Off, Crs, Int, TklW, PKwon, PKcon, OG
  keeper:   SoTA, GA, Saves, Save%, CS

NOT available (per-match): xG, Possession%, Pass completion%, Corners, Clearances
For these fields, use a supplementary provider or manual entry.
"""

import logging
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from app.services.match_stats.provider_base import (
    MatchStatsProvider,
    RawMatchStats,
    TeamMatchStats,
)

logger = logging.getLogger(__name__)

# Cache config
CACHE_DIR = Path.home() / "soccerdata" / "data" / "FBref"


class FBrefProvider(MatchStatsProvider):
    """FBref match statistics via soccerdata scraping (Selenium-based)."""

    provider_name = "fbref"

    def __init__(self):
        self._fb = None

    @property
    def fb(self):
        """Lazy-load FBref handler (Selenium startup is slow)."""
        if self._fb is None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from soccerdata import FBref
                self._fb = FBref(leagues="INT-World Cup", seasons="2026")
        return self._fb

    def supports_xg(self) -> bool:
        return False  # Per-match team logs do NOT include xG

    def supports_possession(self) -> bool:
        return False  # Per-match team logs do NOT include possession

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_match_stats(
        self, match_id: int, home_team: str, away_team: str
    ) -> RawMatchStats:
        """Fetch all available stats for a match, return as RawMatchStats."""
        payload: Dict[str, Any] = {
            "home_team": home_team,
            "away_team": away_team,
            "home_stats": {},
            "away_stats": {},
            "events": {},
        }
        game_id = None

        # 1) Shooting stats
        try:
            shooting = self._fetch_stat_type("shooting")
            home_row = self._find_match_row(shooting, home_team, match_id)
            away_row = self._find_match_row(shooting, away_team, match_id)
            if home_row is not None:
                payload["home_stats"]["shooting"] = home_row
                game_id = game_id or home_row.get("game_id")
            if away_row is not None:
                payload["away_stats"]["shooting"] = away_row
                game_id = game_id or away_row.get("game_id")
        except Exception as e:
            logger.warning(f"FBref shooting fetch failed: {e}")

        # 2) Misc stats (cards, fouls, tackles, interceptions, crosses)
        try:
            misc = self._fetch_stat_type("misc")
            home_row = self._find_match_row(misc, home_team, match_id)
            away_row = self._find_match_row(misc, away_team, match_id)
            if home_row is not None:
                payload["home_stats"]["misc"] = home_row
            if away_row is not None:
                payload["away_stats"]["misc"] = away_row
        except Exception as e:
            logger.warning(f"FBref misc fetch failed: {e}")

        # 3) Keeper stats (saves, clean sheets)
        try:
            keeper = self._fetch_stat_type("keeper")
            home_row = self._find_match_row(keeper, home_team, match_id)
            away_row = self._find_match_row(keeper, away_team, match_id)
            if home_row is not None:
                payload["home_stats"]["keeper"] = home_row
            if away_row is not None:
                payload["away_stats"]["keeper"] = away_row
        except Exception as e:
            logger.warning(f"FBref keeper fetch failed: {e}")

        return RawMatchStats(
            match_id=match_id,
            provider=self.provider_name,
            provider_match_id=game_id,
            source_url=(
                f"https://fbref.com/en/matches/{game_id}/" if game_id else None
            ),
            payload=payload,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    def normalize(
        self, raw: RawMatchStats, side: str
    ) -> TeamMatchStats:
        """Convert raw FBref payload into normalized TeamMatchStats for one side."""
        payload = raw.payload
        key = f"{side}_stats"  # 'home_stats' or 'away_stats'
        stats = payload.get(key, {})
        team_name = payload.get(f"{side}_team", "")

        shooting = stats.get("shooting", {})
        misc_ = stats.get("misc", {})
        keeper = stats.get("keeper", {})

        return TeamMatchStats(
            match_id=raw.match_id,
            team_name=team_name,
            side=side,
            provider=self.provider_name,
            # Offensive
            goals=shooting.get("goals"),
            shots_total=shooting.get("shots"),
            shots_on_target=shooting.get("shots_on_target"),
            # No xG, corners, big_chances from FBref per-match logs
            # Possession & passing — not available
            # Defensive
            tackles=misc_.get("tackles_won"),
            interceptions=misc_.get("interceptions"),
            fouls=misc_.get("fouls"),
            yellow_cards=misc_.get("yellow_cards"),
            red_cards=misc_.get("red_cards"),
            # Goalkeeper
            saves=keeper.get("saves"),
            # Special events
            penalties_awarded=shooting.get("pk_att", 0) or 0,
            penalties_scored=shooting.get("pk", 0) or 0,
            own_goals=misc_.get("own_goals", 0) or 0,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_stat_type(self, stat_type: str) -> pd.DataFrame:
        """Fetch team match stats for a given stat_type, with caching."""
        return self.fb.read_team_match_stats(stat_type=stat_type)

    @staticmethod
    def _find_match_row(
        df: pd.DataFrame, team_name: str, match_id: int
    ) -> Optional[Dict[str, Any]]:
        """Find the row in a multi-index DataFrame matching team + match_id."""
        if df is None or df.empty:
            return None
        try:
            # Index levels: league, season, team, game
            team_level = df.index.names.index("team") if "team" in df.index.names else 2
            mask = df.index.get_level_values(team_level).str.contains(
                team_name, case=False, na=False
            )
            filtered = df[mask]
            if filtered.empty:
                return None
            row = filtered.iloc[-1]  # Take most recent match
            result: Dict[str, Any] = {}
            for col_name, col_value in row.items():
                if isinstance(col_name, tuple):
                    key = col_name[-1].lower().replace("%", "_pct").replace("/", "_per_")
                    # Rename common FBref stat names
                    key = _NORMALIZE_KEY.get(key, key)
                else:
                    key = str(col_name).lower()
                    key = _NORMALIZE_KEY.get(key, key)
                result[key] = col_value
            # Add game_id if present
            if "game" in df.index.names:
                game_idx = df.index.names.index("game")
                result["game_id"] = str(filtered.index[-1][game_idx])
            return result
        except Exception:
            return None


# Mapping from FBref column names → normalized keys
_NORMALIZE_KEY = {
    "gls": "goals",
    "sh": "shots",
    "sot": "shots_on_target",
    "sot_pct": "shots_on_target_pct",
    "g_per_sh": "goal_per_shot",
    "g_per_sot": "goal_per_sot",
    "pk": "pk",
    "pkatt": "pk_att",
    "crdy": "yellow_cards",
    "crdr": "red_cards",
    "2crdy": "second_yellow",
    "fls": "fouls",
    "fld": "fouls_drawn",
    "off": "offsides",
    "crs": "crosses",
    "int": "interceptions",
    "tklw": "tackles_won",
    "pkwon": "pk_won",
    "pkcon": "pk_conceded",
    "og": "own_goals",
    "sota": "shots_on_target_against",
    "ga": "goals_against",
    "save_pct": "save_pct",
    "cs": "clean_sheets",
}
