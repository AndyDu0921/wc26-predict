from __future__ import annotations

from collections import Counter

import pytest

from app.services.tournament_simulator import TournamentSimulator


def test_missing_tournament_probability_fails_closed():
    simulator = TournamentSimulator(runs=1, seed=1)

    with pytest.raises(KeyError, match="placeholder probabilities are prohibited"):
        simulator._simulate_match("France", "Spain", is_group=False)


def test_tournament_probability_resolver_is_stage_aware_and_cached():
    simulator = TournamentSimulator(runs=1, seed=1)
    calls: list[tuple[str, str, bool]] = []

    def resolve(home: str, away: str, is_group: bool) -> dict[str, float]:
        calls.append((home, away, is_group))
        return {"home_win": 0.5, "draw": 0.25, "away_win": 0.25}

    simulator.set_probability_resolver(resolve)
    simulator._get_3way("France", "Spain", is_group=False)
    simulator._get_3way("France", "Spain", is_group=False)
    simulator._get_3way("France", "Spain", is_group=True)

    assert calls == [
        ("France", "Spain", False),
        ("France", "Spain", True),
    ]


def test_simulated_outcome_tracks_supplied_three_way_distribution():
    simulator = TournamentSimulator(runs=1, seed=42)
    simulator.set_match_probability(
        "France",
        "Spain",
        {"home_win": 0.55, "draw": 0.25, "away_win": 0.20},
    )

    counts: Counter[str] = Counter()
    for _ in range(20_000):
        home_goals, away_goals = simulator._simulate_match(
            "France", "Spain", is_group=True
        )
        counts[
            "home" if home_goals > away_goals else "away" if away_goals > home_goals else "draw"
        ] += 1

    assert counts["home"] / 20_000 == pytest.approx(0.55, abs=0.015)
    assert counts["draw"] / 20_000 == pytest.approx(0.25, abs=0.015)
    assert counts["away"] / 20_000 == pytest.approx(0.20, abs=0.015)


def test_tournament_probability_rejects_invalid_values():
    simulator = TournamentSimulator(runs=1, seed=1)

    with pytest.raises(ValueError, match="finite values"):
        simulator.set_match_probability(
            "France",
            "Spain",
            {"home_win": -0.1, "draw": 0.4, "away_win": 0.7},
        )
