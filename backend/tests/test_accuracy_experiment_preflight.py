import json
import sqlite3

from app.services.accuracy_experiment_preflight import run_accuracy_experiment_preflight


def _create_preflight_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE teams (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE matches (
            id TEXT PRIMARY KEY,
            home_team_id TEXT,
            away_team_id TEXT,
            match_date TEXT,
            competition TEXT,
            stage TEXT
        );
        CREATE TABLE match_results (
            match_id TEXT,
            home_goals INTEGER,
            away_goals INTEGER
        );
        CREATE TABLE wc26_schedule (
            id TEXT PRIMARY KEY,
            match_number INTEGER,
            home_team TEXT,
            away_team TEXT,
            match_date TEXT,
            kickoff_time TEXT,
            stage TEXT,
            match_status TEXT,
            home_goals INTEGER,
            away_goals INTEGER
        );
        CREATE TABLE pre_match_snapshots (
            id TEXT PRIMARY KEY,
            match_id TEXT,
            home_team TEXT,
            away_team TEXT,
            snapshot_at TEXT,
            kickoff_at TEXT,
            model_version TEXT,
            weight_config_label TEXT,
            final_home_prob REAL,
            final_draw_prob REAL,
            final_away_prob REAL,
            component_probs TEXT,
            fused_score_matrix TEXT
        );
        CREATE TABLE prediction_snapshots (
            id TEXT PRIMARY KEY,
            match_id TEXT,
            home_team TEXT,
            away_team TEXT,
            generated_at TEXT,
            model_version TEXT,
            adjusted_probs TEXT,
            baseline_probs TEXT,
            component_probs TEXT
        );
        """
    )
    return conn


def _insert_strict_match(conn, idx):
    home = f"Home {idx}"
    away = f"Away {idx}"
    match_id = f"m{idx}"
    kickoff = f"2026-06-{10 + idx:02d}T20:00:00+00:00"
    conn.execute("INSERT INTO teams(id, name) VALUES (?, ?)", (f"h{idx}", home))
    conn.execute("INSERT INTO teams(id, name) VALUES (?, ?)", (f"a{idx}", away))
    conn.execute(
        "INSERT INTO matches(id, home_team_id, away_team_id, match_date, competition, stage) "
        "VALUES (?, ?, ?, ?, 'FIFA World Cup 2026', 'Group')",
        (match_id, f"h{idx}", f"a{idx}", kickoff),
    )
    conn.execute("INSERT INTO match_results(match_id, home_goals, away_goals) VALUES (?, 1, 0)", (match_id,))
    conn.execute(
        "INSERT INTO pre_match_snapshots(id, match_id, home_team, away_team, snapshot_at, kickoff_at, "
        "model_version, weight_config_label, final_home_prob, final_draw_prob, final_away_prob, "
        "component_probs, fused_score_matrix) VALUES (?, ?, ?, ?, ?, ?, '4.9.0-alpha', "
        "'WORLD_CUP_V4.9.0_ALPHA', 0.6, 0.25, 0.15, ?, ?)",
        (
            f"p{idx}",
            match_id,
            home,
            away,
            f"2026-06-{10 + idx:02d}T10:00:00+00:00",
            kickoff,
            json.dumps({"dc": {}}),
            json.dumps([[0.2, 0.1], [0.3, 0.4]]),
        ),
    )


def test_preflight_blocks_when_strict_sample_count_is_too_low(tmp_path):
    db_path = tmp_path / "preflight.db"
    conn = _create_preflight_db(db_path)
    _insert_strict_match(conn, 1)
    conn.commit()
    conn.close()

    payload = run_accuracy_experiment_preflight(db_path, min_sample_count=2)

    assert payload["schema_version"] == "accuracy_experiment_preflight.v1"
    assert payload["status"] == "blocked"
    assert payload["passed"] is False
    blocker_codes = {item["code"] for item in payload["blockers"]}
    assert "insufficient_strict_samples" in blocker_codes
    assert "insufficient_eligible_samples" in blocker_codes


def test_preflight_warns_about_repairable_diagnostics_without_blocking(tmp_path):
    db_path = tmp_path / "preflight.db"
    conn = _create_preflight_db(db_path)
    _insert_strict_match(conn, 1)
    conn.execute(
        "INSERT INTO wc26_schedule(id, match_number, home_team, away_team, match_date, kickoff_time, "
        "stage, match_status, home_goals, away_goals) VALUES "
        "('s2', 2, 'Gamma', 'Delta', '2026-06-20', '20:00', 'Group', 'FINISHED', 0, 0)"
    )
    conn.commit()
    conn.close()

    payload = run_accuracy_experiment_preflight(db_path, min_sample_count=1)

    assert payload["status"] == "ready"
    assert payload["passed"] is True
    warning_codes = {item["code"] for item in payload["warnings"]}
    assert "repairable_diagnostic_samples_present" in warning_codes
    assert payload["registry_summary"]["strict_count"] == 1
    assert payload["repair_summary"]["potentially_promotable_count"] == 1
