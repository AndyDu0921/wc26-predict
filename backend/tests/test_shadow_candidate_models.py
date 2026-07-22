import sqlite3

import pytest

from app.services.shadow_candidate_models import (
    DYNAMIC_DC_MAX_HISTORY_DAYS,
    _load_history,
    _load_world_cup_participant_pool,
    _parse_dt,
    build_shadow_candidate_prediction,
)


def _history_db(path, n=120):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE teams (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            team_type TEXT DEFAULT 'national'
        );
        CREATE TABLE matches (
            id TEXT PRIMARY KEY,
            home_team_id TEXT,
            away_team_id TEXT,
            match_date TEXT,
            stage TEXT,
            is_neutral_venue INTEGER,
            competition_type TEXT DEFAULT 'national'
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
    assert result.reason == "expanding_window_dixon_coles"
    assert sum(result.probs.values()) == pytest.approx(1.0)
    assert result.payload["history_count"] == 120
    assert result.payload["candidate_family"] == "dynamic_goal_model"
    assert result.payload["model_kind"] == "dixon_coles_low_score_correlation"
    assert "rho" in result.payload
    assert len(result.score_matrix) == 11
    assert sum(sum(matrix_row) for matrix_row in result.score_matrix) == pytest.approx(1.0)


def test_dynamic_candidate_excludes_club_history(tmp_path):
    db_path = tmp_path / "history.db"
    _history_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO teams(id, name, team_type) VALUES ('club-a', 'Club A', 'club')")
        conn.execute("INSERT INTO teams(id, name, team_type) VALUES ('club-b', 'Club B', 'club')")
        for idx in range(30):
            conn.execute(
                "INSERT INTO matches(id, home_team_id, away_team_id, match_date, stage, "
                "is_neutral_venue, competition_type) VALUES (?, 'club-a', 'club-b', ?, "
                "'League', 0, 'club')",
                (f"club-{idx}", f"2025-02-{(idx % 28) + 1:02d}T12:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO match_results(match_id, home_goals, away_goals) VALUES (?, 4, 3)",
                (f"club-{idx}",),
            )

    result = build_shadow_candidate_prediction(
        "dynamic_bivariate_poisson",
        {
            "home_team": "Alpha",
            "away_team": "Beta",
            "kickoff_at": "2026-06-01T20:00:00+00:00",
            "current_probs": {"home": 0.4, "draw": 0.3, "away": 0.3},
        },
        db_path=db_path,
    )

    assert result.available is True
    assert result.payload["history_count"] == 120


def test_dynamic_dc_history_uses_participant_pool_and_rolling_window(tmp_path):
    db_path = tmp_path / "history.db"
    _history_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE wc26_schedule (
                stage TEXT,
                home_team TEXT,
                away_team TEXT
            );
            INSERT INTO wc26_schedule VALUES
                ('Group Stage', 'Alpha', 'Beta'),
                ('Group Stage', 'Gamma', 'Delta');
            INSERT INTO matches VALUES
                ('old', 't0', 't1', '2010-01-01T12:00:00+00:00', 'Friendly', 1, 'national');
            INSERT INTO match_results VALUES ('old', 1, 0);
            """
        )

    pool = _load_world_cup_participant_pool(db_path)
    history = _load_history(
        db_path,
        before=_parse_dt("2026-06-01T20:00:00+00:00"),
        team_pool=pool,
        max_age_days=DYNAMIC_DC_MAX_HISTORY_DAYS,
    )

    assert pool == {"Alpha", "Beta", "Gamma", "Delta"}
    assert len(history) == 120
    assert all(match.match_date.year >= 2022 for match in history)


def test_dynamic_bayesian_weighted_goal_alias_is_shadow_only(tmp_path):
    db_path = tmp_path / "history.db"
    _history_db(db_path)
    row = {
        "home_team": "Alpha",
        "away_team": "Beta",
        "kickoff_at": "2026-06-01T20:00:00+00:00",
        "current_probs": {"home": 0.4, "draw": 0.3, "away": 0.3},
    }

    result = build_shadow_candidate_prediction(
        "dynamic_bayesian_weighted_goal_model",
        row,
        db_path=db_path,
    )

    assert result.available is True
    assert result.payload["canonical_candidate_name"] == "bayesian_weighted_dynamic"
    assert result.payload["evolution_method"] == "time_decay_empirical_bayes_shrinkage"
    assert result.payload["shadow_only"] is True


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
    assert result.payload["candidate_family"] == "calibrator"


def test_unknown_shadow_candidate_is_rejected(tmp_path):
    result = build_shadow_candidate_prediction("magic_model", {}, db_path=tmp_path / "missing.db")

    assert result.available is False
    assert result.reason == "unsupported_candidate"


def test_international_covariate_hybrid_requires_feature_training_set(tmp_path):
    row = {
        "home_team": "Alpha",
        "away_team": "Beta",
        "kickoff_at": "2026-06-01T20:00:00+00:00",
        "current_probs": {"home": 0.4, "draw": 0.3, "away": 0.3},
    }

    result = build_shadow_candidate_prediction(
        "international_covariate_hybrid",
        row,
        db_path=tmp_path / "missing.db",
        registry_rows=[],
    )

    assert result.available is False
    assert result.reason == "insufficient_feature_snapshots_0"
    assert result.payload["candidate_family"] == "covariate_hybrid"
    assert result.payload["shadow_only"] is True


def test_player_availability_shadow_has_no_effect_without_relevant_data(tmp_path):
    row = {
        "home_team": "Alpha",
        "away_team": "Beta",
        "as_of_time": "2026-06-29T00:00:00+00:00",
        "current_probs": {"home": 0.45, "draw": 0.30, "away": 0.25},
    }

    result = build_shadow_candidate_prediction(
        "player_availability_shadow",
        row,
        db_path=tmp_path / "missing.db",
    )

    assert result.available is True
    assert result.reason == "no_player_availability_effect"
    assert result.probs == pytest.approx({"home": 0.45, "draw": 0.30, "away": 0.25})
    assert result.payload["source_status"]["shadow_only"] is True


def test_player_availability_shadow_uses_only_pre_asof_records(tmp_path):
    row = {
        "home_team": "Brazil",
        "away_team": "Japan",
        "as_of_time": "2026-06-29T00:00:00+00:00",
        "current_probs": {"home": 0.50, "draw": 0.25, "away": 0.25},
    }
    current_copy = dict(row["current_probs"])

    result = build_shadow_candidate_prediction(
        "player_availability_shadow",
        row,
        db_path=tmp_path / "missing.db",
    )

    assert result.available is True
    assert result.reason == "shadow_player_availability_adjustment"
    assert sum(result.probs.values()) == pytest.approx(1.0)
    assert result.probs != pytest.approx(current_copy)
    assert row["current_probs"] == current_copy
    assert result.payload["source_status"]["shadow_only"] is True
    assert result.payload["source_status"]["excluded_future_records"] == 0
