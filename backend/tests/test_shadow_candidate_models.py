import sqlite3

import pytest

from app.services.shadow_candidate_models import build_shadow_candidate_prediction


def _history_db(path, n=120):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE teams (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE matches (
            id TEXT PRIMARY KEY,
            home_team_id TEXT,
            away_team_id TEXT,
            match_date TEXT,
            stage TEXT,
            is_neutral_venue INTEGER
        );
        CREATE TABLE match_results (match_id TEXT, home_goals INTEGER, away_goals INTEGER);
        """
    )
    teams = ["Alpha", "Beta", "Gamma", "Delta"]
    for idx, team in enumerate(teams):
        conn.execute("INSERT INTO teams(id, name) VALUES (?, ?)", (f"t{idx}", team))
    for idx in range(n):
        home_idx = idx % len(teams)
        away_idx = (idx + 1) % len(teams)
        conn.execute(
            "INSERT INTO matches(id, home_team_id, away_team_id, match_date, stage, is_neutral_venue) "
            "VALUES (?, ?, ?, ?, 'Group', 1)",
            (f"m{idx}", f"t{home_idx}", f"t{away_idx}", f"2025-01-{(idx % 28) + 1:02d}T12:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO match_results(match_id, home_goals, away_goals) VALUES (?, ?, ?)",
            (f"m{idx}", 2 if home_idx == 0 else 1, 0 if away_idx == 1 else 1),
        )
    conn.commit()
    conn.close()


def test_dynamic_dixon_coles_shadow_candidate_uses_history(tmp_path):
    db_path = tmp_path / "history.db"
    _history_db(db_path)
    row = {
        "home_team": "Alpha",
        "away_team": "Beta",
        "kickoff_at": "2026-06-01T20:00:00+00:00",
        "current_probs": {"home": 0.4, "draw": 0.3, "away": 0.3},
    }

    result = build_shadow_candidate_prediction("dynamic_dixon_coles", row, db_path=db_path)

    assert result.available is True
    assert result.reason == "computed_from_pre_match_history"
    assert sum(result.probs.values()) == pytest.approx(1.0)
    assert result.payload["history_count"] == 120


def test_dirichlet_calibration_is_unavailable_without_prior_samples(tmp_path):
    db_path = tmp_path / "history.db"
    _history_db(db_path)
    row = {
        "home_team": "Alpha",
        "away_team": "Beta",
        "kickoff_at": "2026-06-01T20:00:00+00:00",
        "current_probs": {"home": 0.4, "draw": 0.3, "away": 0.3},
    }

    result = build_shadow_candidate_prediction("dirichlet_calibration", row, db_path=db_path, registry_rows=[])

    assert result.available is False
    assert result.reason.startswith("insufficient_prior_paired_samples")


def test_unknown_shadow_candidate_is_rejected(tmp_path):
    result = build_shadow_candidate_prediction("magic_model", {}, db_path=tmp_path / "missing.db")

    assert result.available is False
    assert result.reason == "unsupported_candidate"
