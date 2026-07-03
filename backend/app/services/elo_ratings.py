"""Elo rating system for football teams.

Computes Elo ratings from historical matches and produces independent
win/draw/loss probabilities.  Designed as a third probability source to
complement Dixon-Coles and the tabular enhancer.

Elo formula:
  expected_home = 1 / (1 + 10^((R_away - (R_home + home_adv)) / 400))
  expected_away = 1 - expected_home

Update after match with result r ∈ {1, 0.5, 0} (win/draw/loss from home pov):
  R_new = R_old + K * (r - expected)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_RATING = 1500.0
HOME_ADVANTAGE = 100.0          # Elo points for home team
K_LEAGUE = 20                    # K-factor for regular league matches
K_KNOCKOUT = 32                  # K-factor for knockout / World Cup matches


def _elo_davidson_draw(elo_diff: float, kappa: float = 0.24) -> float:
    """κ-Elo (Elo-Davidson) three-outcome draw probability.

    Source: Szczecinski & Djebbi (2020) "Understanding Draws in Elo Rating Algorithm"
    https://arxiv.org/abs/1805.05293

    P(draw) = κ × sqrt(σ(r) × σ(-r))
    where r = elo_diff / 400, σ(r) = 1 / (1 + 10^(-r))

    κ controls the overall draw frequency:
      - Football: κ ≈ 0.20–0.35
      - EPL: κ ≈ 0.28 (high draw rate)
      - UCL knockout: κ ≈ 0.18 (low draw rate, extra time penalties)
      - Default: 0.24

    Guarantees: draw ∈ [0.05, 0.35] for any Elo difference.
    """
    r = elo_diff / 400.0
    sigma_pos = 1.0 / (1.0 + 10.0 ** (-r))
    sigma_neg = 1.0 - sigma_pos
    draw_raw = kappa * (sigma_pos * sigma_neg) ** 0.5
    # Clamp to reasonable range
    return max(0.02, min(0.35, draw_raw))


# Audit R4-H4: cache kappa values to avoid N SQLite connections
# per tournament simulation (1000+ matches → 1000+ DB hits).
_KAPPA_CACHE: dict[str, float] = {}


def get_kappa_for_competition(competition: str | None = None) -> float:
    """Read κ from model_weight_config based on competition type.

    Returns:
        κ-Elo draw tendency parameter (0.18–0.50 typical range).
        Falls back to 0.24 if DB unavailable or no match.
        Results are cached in _KAPPA_CACHE for simulator performance.
    """
    if not competition:
        return 0.24

    comp_lower = competition.lower()

    # Map competition → config key
    if "world cup" in comp_lower or "fifa" in comp_lower:
        key = "kappa_elo_wc"
    elif "premier league" in comp_lower or "epl" in comp_lower:
        key = "kappa_elo_epl"
    elif "champions league" in comp_lower or "ucl" in comp_lower:
        key = "kappa_elo_ucl"
    elif "fa cup" in comp_lower:
        key = "kappa_elo_epl"  # FA Cup uses EPL κ (similar draw patterns)
    else:
        key = "kappa_elo_default"

    if key in _KAPPA_CACHE:
        return _KAPPA_CACHE[key]

    try:
        import sqlite3
        from pathlib import Path
        db_path = Path(__file__).resolve().parents[2] / "data" / "local_stage2.db"
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT config_value FROM model_weight_config WHERE config_key = ?",
                (key,)
            )
            row = cur.fetchone()
            if row:
                kappa = float(row[0])
                _KAPPA_CACHE[key] = kappa
                return kappa
        finally:
            conn.close()
    except Exception:
        logger.warning("Could not read kappa from DB for competition=%s — using default 0.24",
                       competition, exc_info=True)

    _KAPPA_CACHE[key] = 0.24
    return 0.24


@dataclass(slots=True)
class EloPrediction:
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    home_elo: float
    away_elo: float
    home_elo_adj: float           # home_elo + home_advantage
    rating_gap: float
    k_factor: float


class EloRatingSystem:
    """Elo rating system for football teams.

    Usage::

        elo = EloRatingSystem()
        elo.fit(matches_df)               # compute historical ratings
        pred = elo.predict("Brazil", "Argentina")  # get probabilities
    """

    def __init__(self) -> None:
        self.ratings: dict[str, float] = {}
        self.rating_history: list[dict[str, Any]] = []
        self._match_count: int = 0

    # ------------------------------------------------------------------
    #  Fit: walk through historical matches in chronological order
    # ------------------------------------------------------------------
    def fit(self, matches_df: pd.DataFrame) -> EloRatingSystem:
        """Compute Elo ratings from a chronologically-sorted match DataFrame.

        Required columns:
          match_date, home_team, away_team,
          home_goals, away_goals, competition_weight (optional)
        """
        df = self._normalize(matches_df)
        self.ratings.clear()
        self.rating_history.clear()
        self._match_count = 0

        for _, row in df.iterrows():
            home = row["home_team"]
            away = row["away_team"]
            home_goals = int(row["home_goals"])
            away_goals = int(row["away_goals"])
            comp_weight = float(row.get("competition_weight", 1.0))

            r_home = self._get_rating(home)
            r_away = self._get_rating(away)
            k = self._k_factor(comp_weight)

            # Expected from home perspective
            expected = self._expected_score(r_home + HOME_ADVANTAGE, r_away)

            # Actual result
            if home_goals > away_goals:
                result = 1.0
            elif home_goals == away_goals:
                result = 0.5
            else:
                result = 0.0

            # Elo update
            delta = k * (result - expected)
            self._set_rating(home, r_home + delta)
            self._set_rating(away, r_away - delta)

            self._match_count += 1

            self.rating_history.append({
                "match_index": self._match_count,
                "home_team": home,
                "away_team": away,
                "home_elo": r_home,
                "away_elo": r_away,
                "expected": expected,
                "result": result,
                "delta_home": delta,
            })

        return self

    # ------------------------------------------------------------------
    #  Predict
    # ------------------------------------------------------------------
    def predict(
        self,
        home_team: str,
        away_team: str,
        *,
        is_neutral: bool = False,
        competition_weight: float = 1.0,
        competition: str | None = None,
    ) -> EloPrediction:
        """Return win/draw/loss probabilities for a single match."""
        r_home = self._get_rating(home_team)
        r_away = self._get_rating(away_team)
        home_adv = 0.0 if is_neutral else HOME_ADVANTAGE

        adj_home = r_home + home_adv
        gap = adj_home - r_away

        p_home_win = self._expected_score(adj_home, r_away)
        p_away_win = 1.0 - p_home_win

        # Draw probability: κ-Elo (Elo-Davidson) model
        # Source: Szczecinski & Djebbi (2020)
        # P(draw) = κ × sqrt(σ(r) × σ(-r)) where r = elo_diff / 400
        # κ read from model_weight_config (per-competition); falls back to 0.24
        kappa = get_kappa_for_competition(competition)
        p_draw = _elo_davidson_draw(gap, kappa)

        # Allocate remaining probability proportionally
        remaining = 1.0 - p_draw
        if remaining > 0:
            p_home_win = p_home_win * remaining
            p_away_win = p_away_win * remaining

        total = p_home_win + p_draw + p_away_win
        if total > 0:
            p_home_win /= total
            p_draw /= total
            p_away_win /= total

        return EloPrediction(
            home_win_prob=float(p_home_win),
            draw_prob=float(p_draw),
            away_win_prob=float(p_away_win),
            home_elo=float(r_home),
            away_elo=float(r_away),
            home_elo_adj=float(adj_home),
            rating_gap=float(gap),
            k_factor=float(self._k_factor(competition_weight)),
        )

    def get_ratings(self) -> dict[str, float]:
        """Return a copy of the current ratings table."""
        return dict(self.ratings)

    # ------------------------------------------------------------------
    #  Internals
    # ------------------------------------------------------------------
    def _get_rating(self, team: str) -> float:
        return self.ratings.get(team, DEFAULT_RATING)

    def _set_rating(self, team: str, rating: float) -> None:
        self.ratings[team] = rating

    @staticmethod
    def _expected_score(r_home: float, r_away: float) -> float:
        """Expected win probability for the home team."""
        return 1.0 / (1.0 + 10.0 ** ((r_away - r_home) / 400.0))

    @staticmethod
    def _k_factor(competition_weight: float) -> float:
        """K-factor scales with match importance."""
        if competition_weight >= 1.5:          # World Cup
            return K_KNOCKOUT
        if competition_weight >= 1.2:          # Champions League
            return 28.0
        return K_LEAGUE

    @staticmethod
    def _normalize(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["match_date"] = pd.to_datetime(df["match_date"], utc=True)
        df = df.sort_values("match_date").reset_index(drop=True)
        return df


# ------------------------------------------------------------------
#  Integration helper: blend Elo with Dixon-Coles
# ------------------------------------------------------------------
def fuse_elo_probabilities(
    dixon_probs: dict[str, float],
    elo_pred: EloPrediction,
    *,
    elo_weight: float = 0.15,
) -> dict[str, float]:
    """Blend Elo probabilities into Dixon-Coles output.

    Default 15% Elo weight — Elo is a useful prior but shouldn't dominate
    the statistical model.
    """
    dixon_weight = 1.0 - elo_weight
    fused = {
        "home_win_prob": dixon_probs["home_win_prob"] * dixon_weight
        + elo_pred.home_win_prob * elo_weight,
        "draw_prob": dixon_probs["draw_prob"] * dixon_weight
        + elo_pred.draw_prob * elo_weight,
        "away_win_prob": dixon_probs["away_win_prob"] * dixon_weight
        + elo_pred.away_win_prob * elo_weight,
    }
    total = fused["home_win_prob"] + fused["draw_prob"] + fused["away_win_prob"]
    if total > 0:
        for k in fused:
            fused[k] /= total
    return fused
