import json
import sqlite3

from app.services.evaluation_registry_repair import build_evaluation_registry_repair_report


def _create_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE teams (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE matches (
            id TEXT PRIMARY KEY,
            home_team_id TEXT,
            away_team_id TEXT,
            match_date TEXT,
            competition TEXT,
            stage TEXT
        );
        CREATE TABLE match_results (match_id TEXT, home_goals INTEGER, away_goals INTEGER);
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


def test_repair_report_marks_missing_snapshot_as_real_evidence_repair(tmp_path):
    db_path = tmp_path / "registry.db"
    conn = _create_db(db_path)
    conn.execute(
        "INSERT INTO wc26_schedule(id, match_number, home_team, away_team, match_date, kickoff_time, stage, match_status, home_goals, away_goals) "
        "VALUES ('s1', 1, 'Alpha', 'Beta', '2026-07-01', '20:00', 'Group', 'FINISHED', 1, 0)"
    )
    conn.commit()
    conn.close()

    report = build_evaluation_registry_repair_report(db_path)

    assert report["schema_version"] == "evaluation_registry_repair_report.v2"
    assert report["repair_summary"]["reported_samples"] == 1
    assert report["repair_summary"]["priority_counts"]["P0"] == 1
    assert report["repair_summary"]["blocking_level_counts"]["repairable"] == 1
    row = report["samples"][0]
    assert row["sample_status"] == "diagnostic"
    assert row["priority"] == "P0"
    assert row["blocking_level"] == "repairable"
    assert row["repair_order"] == 30
    assert row["can_promote_to_strict_after_actions"] is True
    assert row["promotability_reason"] == "requires_real_pre_kickoff_probability_and_timestamp_evidence"
    actions = {item["action"] for item in row["recommended_actions"]}
    assert "import_real_pre_match_snapshot" in actions
    assert "recover_current_probabilities_from_valid_snapshot" in actions
    grouped_actions = {item["action"] for item in report["repair_summary"]["action_groups"]}
    assert "normalize_snapshot_and_kickoff_time" in grouped_actions


def test_repair_report_does_not_promote_post_kickoff_snapshot(tmp_path):
    db_path = tmp_path / "registry.db"
    conn = _create_db(db_path)
    conn.execute("INSERT INTO teams(id, name) VALUES ('h1', 'Alpha')")
    conn.execute("INSERT INTO teams(id, name) VALUES ('a1', 'Beta')")
    conn.execute(
        "INSERT INTO matches(id, home_team_id, away_team_id, match_date, competition, stage) "
        "VALUES ('m1', 'h1', 'a1', '2026-06-15T20:00:00+00:00', 'FIFA World Cup 2026', 'Group')"
    )
    conn.execute("INSERT INTO match_results(match_id, home_goals, away_goals) VALUES ('m1', 1, 0)")
    conn.execute(
        "INSERT INTO prediction_snapshots(id, match_id, home_team, away_team, generated_at, model_version, adjusted_probs, baseline_probs, component_probs) "
        "VALUES ('ps1', 'm1', 'Alpha', 'Beta', '2026-06-15T22:00:00+00:00', '4.8.0-alpha', ?, NULL, ?)",
        (
            json.dumps({"home": 0.6, "draw": 0.25, "away": 0.15}),
            json.dumps({"dc": {}}),
        ),
    )
    conn.commit()
    conn.close()

    row = build_evaluation_registry_repair_report(db_path)["samples"][0]

    assert row["sample_status"] == "rejected"
    assert row["blocking_level"] == "hard_block"
    assert row["can_promote_to_strict_after_actions"] is False
    assert "snapshot_after_kickoff" in row["exclusion_reasons"]
    assert row["promotability_reason"] == "hard_blocked_by_snapshot_after_kickoff"
    assert row["promotion_policy"] == "Do not promote without replacing hard-blocking evidence."
