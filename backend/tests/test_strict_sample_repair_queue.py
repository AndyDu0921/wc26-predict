import json
import sqlite3

from app.services.strict_sample_repair_queue import build_strict_sample_repair_queue


def _create_queue_db(path):
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


def test_repair_queue_marks_missing_local_evidence_as_external_needed(tmp_path):
    db_path = tmp_path / "queue.db"
    conn = _create_queue_db(db_path)
    conn.execute(
        "INSERT INTO wc26_schedule(id, match_number, home_team, away_team, match_date, kickoff_time, "
        "stage, match_status, home_goals, away_goals) VALUES "
        "('s1', 1, 'Alpha', 'Beta', '2026-06-15', '20:00', 'Group', 'FINISHED', 1, 0)"
    )
    conn.commit()
    conn.close()

    payload = build_strict_sample_repair_queue(db_path)

    assert payload["schema_version"] == "strict_sample_repair_queue.v1"
    assert payload["summary"]["needs_external_evidence_count"] == 1
    row = payload["samples"][0]
    assert row["repair_class"] == "needs_external_pre_match_snapshot"
    assert row["local_evidence_status"] == "no_local_snapshot_candidates"
    assert row["can_repair_from_local_evidence"] is False


def test_repair_queue_does_not_accept_candidate_missing_probabilities(tmp_path):
    db_path = tmp_path / "queue.db"
    conn = _create_queue_db(db_path)
    conn.execute("INSERT INTO teams(id, name) VALUES ('h1', 'Alpha')")
    conn.execute("INSERT INTO teams(id, name) VALUES ('a1', 'Beta')")
    conn.execute(
        "INSERT INTO matches(id, home_team_id, away_team_id, match_date, competition, stage) "
        "VALUES ('m1', 'h1', 'a1', '2026-06-15T20:00:00+00:00', 'FIFA World Cup 2026', 'Group')"
    )
    conn.execute("INSERT INTO match_results(match_id, home_goals, away_goals) VALUES ('m1', 1, 0)")
    conn.execute(
        "INSERT INTO prediction_snapshots(id, match_id, home_team, away_team, generated_at, model_version, "
        "adjusted_probs, baseline_probs, component_probs) VALUES "
        "('ps1', 'm1', 'Alpha', 'Beta', '2026-06-15T10:00:00+00:00', '4.9.0-alpha', NULL, NULL, ?)",
        (json.dumps({"dc": {}}),),
    )
    conn.commit()
    conn.close()

    row = build_strict_sample_repair_queue(db_path)["samples"][0]

    assert row["sample_status"] == "diagnostic"
    assert row["local_evidence_status"] == "local_candidates_found_but_not_strict_usable"
    assert row["local_evidence"]["candidate_count"] == 1
    assert row["local_evidence"]["usable_candidate_count"] == 0
    assert row["local_evidence"]["blocked_candidates"][0]["block_reasons"] == ["missing_current_probabilities"]


def test_repair_queue_hard_blocks_post_kickoff_snapshot(tmp_path):
    db_path = tmp_path / "queue.db"
    conn = _create_queue_db(db_path)
    conn.execute("INSERT INTO teams(id, name) VALUES ('h1', 'Alpha')")
    conn.execute("INSERT INTO teams(id, name) VALUES ('a1', 'Beta')")
    conn.execute(
        "INSERT INTO matches(id, home_team_id, away_team_id, match_date, competition, stage) "
        "VALUES ('m1', 'h1', 'a1', '2026-06-15T20:00:00+00:00', 'FIFA World Cup 2026', 'Group')"
    )
    conn.execute("INSERT INTO match_results(match_id, home_goals, away_goals) VALUES ('m1', 1, 0)")
    conn.execute(
        "INSERT INTO prediction_snapshots(id, match_id, home_team, away_team, generated_at, model_version, "
        "adjusted_probs, baseline_probs, component_probs) VALUES "
        "('ps1', 'm1', 'Alpha', 'Beta', '2026-06-15T22:00:00+00:00', '4.9.0-alpha', ?, NULL, ?)",
        (
            json.dumps({"home": 0.6, "draw": 0.25, "away": 0.15}),
            json.dumps({"dc": {}}),
        ),
    )
    conn.commit()
    conn.close()

    row = build_strict_sample_repair_queue(db_path)["samples"][0]

    assert row["sample_status"] == "rejected"
    assert row["repair_class"] == "hard_block_requires_replacement_or_result_reconcile"
    assert row["blocking_level"] == "hard_block"
    assert row["can_repair_from_local_evidence"] is False
    assert row["local_evidence"]["blocked_candidates"][0]["block_reasons"] == ["candidate_after_kickoff"]
