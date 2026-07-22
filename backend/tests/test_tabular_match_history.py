from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from app.services.prediction_pipeline import _coerce_match_datetime
from app.services.tabular_match_model import TabularMatchEnhancer


def test_team_profile_excludes_matches_at_or_after_prediction_cutoff():
    history = pd.DataFrame(
        [
            {
                "home_team": "Alpha",
                "away_team": "Beta",
                "home_goals": 1,
                "away_goals": 0,
                "home_xg": 1.1,
                "away_xg": 0.4,
                "match_date": pd.Timestamp("2026-01-01T12:00:00Z"),
            },
            {
                "home_team": "Alpha",
                "away_team": "Beta",
                "home_goals": 9,
                "away_goals": 9,
                "home_xg": 9.0,
                "away_xg": 9.0,
                "match_date": pd.Timestamp("2026-02-01T12:00:00Z"),
            },
        ]
    )

    profile = TabularMatchEnhancer()._team_profile(
        history,
        "Alpha",
        datetime(2026, 1, 15, tzinfo=UTC),
    )

    assert profile["matches_played"] == 1
    assert profile["goals_for_avg"] == pytest.approx(1.0)
    assert profile["goals_against_avg"] == pytest.approx(0.0)


def test_match_datetime_normalizes_naive_values_to_utc():
    parsed = _coerce_match_datetime("2026-07-19T03:00:00")

    assert parsed is not None
    assert parsed.tzinfo is UTC
