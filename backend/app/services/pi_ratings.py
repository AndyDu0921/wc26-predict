"""Pi-Rating system via penaltyblog library.

Pi-Ratings are zero-centered: positive = better than average, negative = worse.
Responds to goal DIFFERENCE, not just win/loss — a 5-0 win generates a larger
rating change than a 1-0 win, with diminishing returns to prevent blowouts
from dominating.

Reference: Constantinou & Fenton (2012)
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

PROB_SCALE = 0.35


class PiRatingWrapper:
    """Wrapper around penaltyblog PiRatingSystem for WC26 pipeline."""

    def __init__(self, k: float = 0.1):
        self.k = k
        self.team_ratings: dict[str, float] = {}
        self._match_count: int = 0

    def fit(self, df: pd.DataFrame) -> PiRatingWrapper:
        """Fit Pi-Ratings on historical match data."""
        from penaltyblog.ratings import PiRatingSystem

        pi = PiRatingSystem(k=self.k)

        sorted_df = df.sort_values("match_date") if "match_date" in df.columns else df

        for _, row in sorted_df.iterrows():
            try:
                home = str(row["home_team"])
                away = str(row["away_team"])
                # Skip rows with NaN/missing goals — recording a fake 0-0 draw
                # would silently corrupt the rating database (audit R3 M1).
                if pd.isna(row.get("home_goals")) or pd.isna(row.get("away_goals")):
                    logger.warning(
                        "Skipping Pi-rating row with NaN goals: %s vs %s",
                        home, away,
                    )
                    continue
                hg = int(row["home_goals"])
                ag = int(row["away_goals"])
                pi.update_ratings(home, away, hg, ag)
                self._match_count += 1
            except Exception as exc:
                logger.warning("Skipping malformed Pi-rating match row: %s", exc)
                continue

        self.team_ratings = {
            t: (v["home"] + v["away"]) / 2.0
            for t, v in pi.team_ratings.items()
        }
        logger.info("Pi-Rating fitted on %d matches, %d teams", self._match_count, len(self.team_ratings))
        return self

    def predict(self, home_team: str, away_team: str, is_neutral: bool = False) -> dict[str, float]:
        """Predict match outcome from Pi-Ratings."""
        home_r = self.team_ratings.get(home_team, 0.0)
        away_r = self.team_ratings.get(away_team, 0.0)

        home_adj = 0.0 if is_neutral else 0.3
        xg_diff = (home_r + home_adj - away_r) * PROB_SCALE * 2.0

        home_win_raw = 1.0 / (1.0 + np.exp(-xg_diff * 2.5))
        away_win_raw = 1.0 / (1.0 + np.exp(xg_diff * 2.5))
        draw_raw = 0.26 * np.exp(-(xg_diff ** 2) / 2.0)

        total = home_win_raw + draw_raw + away_win_raw
        return {
            "home_win_prob": float(home_win_raw / total),
            "draw_prob": float(draw_raw / total),
            "away_win_prob": float(away_win_raw / total),
        }

    def get_ratings_dict(self) -> dict[str, float]:
        return dict(self.team_ratings)


def fuse_pi_probabilities(
    base_probs: dict[str, float],
    pi_pred: dict[str, float],
    pi_weight: float = 0.10,
) -> dict[str, float]:
    """Blend Pi-Rating into the existing fused model output."""
    import copy
    fused = copy.deepcopy(base_probs)
    for key in ("home_win_prob", "draw_prob", "away_win_prob"):
        fused[key] = base_probs[key] * (1.0 - pi_weight) + pi_pred[key] * pi_weight
    total = fused["home_win_prob"] + fused["draw_prob"] + fused["away_win_prob"]
    for key in ("home_win_prob", "draw_prob", "away_win_prob"):
        fused[key] /= total
    return fused
