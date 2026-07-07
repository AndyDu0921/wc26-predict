"""Shared walk-forward Elo / Pi helpers for historical backtest scripts.

The current V4.9 accuracy path uses ``run_accuracy_experiments.py`` and the
evaluation registry.  These helpers remain for historical reproduction and
manual diagnostics that need incremental no-lookahead Elo/Pi ratings.

All functions are deterministic and IO-free (except the shared data-loaders
that read from SQLite via a caller-supplied path).
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

# ── Constants ──────────────────────────────────────────────────────────

DEFAULT_ELO = 1500.0
ELO_HOME_ADVANTAGE = 100.0
ELO_K_FACTOR = 32.0
DC_HALF_LIFE = 180

WC26_COMPETITION = "FIFA World Cup 2026"
KO_STAGES = frozenset({
    "Round of 32", "Round of 16", "Quarter-finals",
    "Semi-finals", "Final", "Third Place",
})


# ═══════════════════════════════════════════════════════════════════════════
#  Data loading
# ═══════════════════════════════════════════════════════════════════════════

def load_all_training_data(
    db_path: Path, min_date: str = "2020-01-01",
) -> pd.DataFrame:
    """Load finished national-team matches from SQLite."""
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    query = f"""
        SELECT ht.name AS home_team,
               at.name AS away_team,
               mr.home_goals,
               mr.away_goals,
               m.match_date,
               COALESCE(m.competition_weight, 1.0) AS competition_weight,
               COALESCE(m.is_neutral_venue, 0)     AS is_neutral_venue,
               m.competition,
               m.competition_type,
               m.stage
        FROM matches m
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
        JOIN match_results mr ON m.id = mr.match_id
        WHERE m.status = 'finished'
          AND ht.team_type = 'national'
          AND at.team_type = 'national'
          AND m.match_date >= '{min_date}'
        ORDER BY m.match_date ASC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()

    df["match_date"] = pd.to_datetime(df["match_date"], utc=True, format="mixed")
    print(f"  Loaded {len(df):,} training matches, {df.home_team.nunique()} teams")
    return df


def load_wc26_eval_matches(
    db_path: Path, competition: str = WC26_COMPETITION,
) -> pd.DataFrame:
    """Load WC26 matches that have results (evaluation targets)."""
    conn = sqlite3.connect(str(db_path))
    query = """
        SELECT ht.name AS home_team,
               at.name AS away_team,
               mr.home_goals,
               mr.away_goals,
               m.match_date,
               COALESCE(m.is_neutral_venue, 1) AS is_neutral_venue,
               m.stage
        FROM matches m
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
        JOIN match_results mr ON m.id = mr.match_id
        WHERE m.competition = ?
        ORDER BY m.match_date ASC
    """
    df = pd.read_sql_query(query, conn, params=(competition,))
    conn.close()

    df["match_date"] = pd.to_datetime(df["match_date"], utc=True, format="mixed")
    group_count = sum(1 for s in df["stage"] if s not in KO_STAGES)
    ko_count = sum(1 for s in df["stage"] if s in KO_STAGES)
    print(f"  WC26 evaluation matches: {len(df)} ({group_count} group + {ko_count} KO)")
    return df


# ═══════════════════════════════════════════════════════════════════════════
#  Elo computation (walk-forward, incremental from match history)
# ═══════════════════════════════════════════════════════════════════════════

def expected_score(r_home: float, r_away: float) -> float:
    """Expected win probability for the home team."""
    return 1.0 / (1.0 + 10.0 ** ((r_away - r_home) / 400.0))


def elo_davidson_draw(gap: float, kappa: float = 0.30) -> float:
    """Elo-Davidson draw probability (WC kappa=0.30)."""
    r = gap / 400.0
    p_draw = kappa * math.sqrt(
        (1.0 / (1.0 + 10.0 ** (-r))) *
        (1.0 / (1.0 + 10.0 ** r))
    )
    return float(p_draw)


def compute_elo_probs(
    home_team: str,
    away_team: str,
    ratings: dict[str, float],
    is_neutral: bool = True,
    kappa: float = 0.30,
) -> dict[str, float]:
    """Compute Elo win/draw/loss probabilities from ratings.

    Returns keys: ``home_win_prob``, ``draw_prob``, ``away_win_prob``.
    """
    r_home = ratings.get(home_team, DEFAULT_ELO)
    r_away = ratings.get(away_team, DEFAULT_ELO)
    home_adv = 0.0 if is_neutral else ELO_HOME_ADVANTAGE

    adj_home = r_home + home_adv
    gap = adj_home - r_away

    p_home_win = expected_score(adj_home, r_away)
    p_away_win = 1.0 - p_home_win
    p_draw = elo_davidson_draw(gap, kappa)

    remaining = 1.0 - p_draw
    if remaining > 0:
        p_home_win = p_home_win * remaining
        p_away_win = p_away_win * remaining

    total = p_home_win + p_draw + p_away_win
    if total > 0:
        p_home_win /= total
        p_draw /= total
        p_away_win /= total

    return {
        "home_win_prob": float(p_home_win),
        "draw_prob": float(p_draw),
        "away_win_prob": float(p_away_win),
    }


def update_elo_ratings(
    ratings: dict[str, float],
    home_team: str,
    away_team: str,
    home_goals: int,
    away_goals: int,
    is_neutral: bool = True,
    k_factor: float = ELO_K_FACTOR,
) -> None:
    """Update Elo ratings for a single match result (mutates *ratings*)."""
    r_home = ratings.get(home_team, DEFAULT_ELO)
    r_away = ratings.get(away_team, DEFAULT_ELO)
    home_adv = 0.0 if is_neutral else ELO_HOME_ADVANTAGE

    adj_home = r_home + home_adv
    e_home = expected_score(adj_home, r_away)
    e_away = 1.0 - e_home

    # Actual outcome: 1=home win, 0.5=draw, 0=away win
    if home_goals > away_goals:
        s_home, s_away = 1.0, 0.0
    elif home_goals == away_goals:
        s_home, s_away = 0.5, 0.5
    else:
        s_home, s_away = 0.0, 1.0

    # Goal differential multiplier
    goal_diff = abs(home_goals - away_goals)
    g_mult = 1.0
    if goal_diff == 2:
        g_mult = 1.5
    elif goal_diff >= 3:
        g_mult = 1.75

    ratings[home_team] = r_home + k_factor * g_mult * (s_home - e_home)
    ratings[away_team] = r_away + k_factor * g_mult * (s_away - e_away)


def build_elo_ratings_as_of(
    history_df: pd.DataFrame,
    cutoff_date: pd.Timestamp,
) -> dict[str, float]:
    """Build Elo ratings incrementally from all matches before *cutoff_date*."""
    ratings: dict[str, float] = {}
    pre_df = history_df[history_df["match_date"] < cutoff_date]
    for row in pre_df.itertuples(index=False):
        update_elo_ratings(
            ratings,
            row.home_team, row.away_team,
            int(row.home_goals), int(row.away_goals),
            is_neutral=bool(row.is_neutral_venue),
        )
    return ratings


# ═══════════════════════════════════════════════════════════════════════════
#  Pi-Rating computation (walk-forward, incremental)
# ═══════════════════════════════════════════════════════════════════════════

def compute_pi_probs(
    home_team: str,
    away_team: str,
    pi_ratings: dict[str, float],
    is_neutral: bool = True,
) -> dict[str, float]:
    """Compute Pi win/draw/loss probabilities from Pi ratings.

    Pi ratings are z-scores (mean≈0, std≈1). Higher = stronger team.
    The rating difference maps to probabilities via a sigmoid.

    Returns keys: ``home_win_prob``, ``draw_prob``, ``away_win_prob``.
    """
    r_home = pi_ratings.get(home_team, 0.0)
    r_away = pi_ratings.get(away_team, 0.0)

    # Neutral venue: no home advantage in Pi
    home_adj = 0.0 if is_neutral else 0.3
    xg_diff = (r_home + home_adj - r_away) * 0.35

    # Sigmoid for win probability
    p_home_win = 1.0 / (1.0 + math.exp(-xg_diff * 2.5))
    p_away_win = 1.0 / (1.0 + math.exp(xg_diff * 2.5))

    # Draw: exponential decay with xG difference
    p_draw = 0.26 * math.exp(-xg_diff * xg_diff / 2.0)

    # Normalize
    total = p_home_win + p_draw + p_away_win
    if total > 0:
        p_home_win /= total
        p_draw /= total
        p_away_win /= total

    return {
        "home_win_prob": float(p_home_win),
        "draw_prob": float(p_draw),
        "away_win_prob": float(p_away_win),
    }


def update_pi_ratings(
    pi_ratings: dict[str, float],
    home_team: str,
    away_team: str,
    home_goals: int,
    away_goals: int,
    is_neutral: bool = True,
    k: float = 0.1,
) -> None:
    """Update Pi ratings for a single match result (mutates *pi_ratings*).

    Pi uses a simplified Elo-like update with Elo weight k=0.1.
    """
    r_home = pi_ratings.get(home_team, 0.0)
    r_away = pi_ratings.get(away_team, 0.0)
    home_adj = 0.0 if is_neutral else 0.3

    xg_diff = (r_home + home_adj - r_away) * 0.35
    e_home = 1.0 / (1.0 + math.exp(-xg_diff * 2.5))
    e_away = 1.0 - e_home

    if home_goals > away_goals:
        s_home, s_away = 1.0, 0.0
    elif home_goals == away_goals:
        s_home, s_away = 0.5, 0.5
    else:
        s_home, s_away = 0.0, 1.0

    goal_diff = abs(home_goals - away_goals)
    g_mult = min(2.0, 1.0 + goal_diff * 0.25)

    pi_ratings[home_team] = r_home + k * g_mult * (s_home - e_home)
    pi_ratings[away_team] = r_away + k * g_mult * (s_away - e_away)


def build_pi_ratings_as_of(
    history_df: pd.DataFrame,
    cutoff_date: pd.Timestamp,
) -> dict[str, float]:
    """Build Pi ratings incrementally from all matches before *cutoff_date*."""
    pi_ratings: dict[str, float] = {}
    pre_df = history_df[history_df["match_date"] < cutoff_date]
    for row in pre_df.itertuples(index=False):
        update_pi_ratings(
            pi_ratings,
            row.home_team, row.away_team,
            int(row.home_goals), int(row.away_goals),
            is_neutral=bool(row.is_neutral_venue),
        )
    return pi_ratings
