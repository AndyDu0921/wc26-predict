import json
import sqlite3

from app.services.feature_snapshot_materializer import (
    build_feature_snapshot_records,
    persist_feature_snapshot_records,
)


def _create_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
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
        CREATE TABLE feature_snapshots (
            id TEXT PRIMARY KEY,
            created_at TEXT,
            updated_at TEXT,
            sample_id TEXT NOT NULL,
            match_id TEXT,
            source TEXT NOT NULL,
            as_of_time TEXT,
            kickoff_at TEXT,
            horizon_hours REAL,
            feature_hash TEXT NOT NULL,
            payload TEXT NOT NULL,
            data_availability TEXT NOT NULL,
            leakage_status TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO wc26_schedule(id, match_number, home_team, away_team, match_date, kickoff_time, "
        "stage, match_status, home_goals, away_goals) VALUES "
        "('s1', 1, 'Alpha', 'Beta', '2026-07-01', '20:00', 'Round of 32', 'FINISHED', 2, 0)"
    )
    conn.execute(
        "INSERT INTO pre_match_snapshots(id, match_id, home_team, away_team, snapshot_at, kickoff_at, "
        "model_version, weight_config_label, final_home_prob, final_draw_prob, final_away_prob, "
        "component_probs, fused_score_matrix) VALUES "
        "('p1', 's1', 'Alpha', 'Beta', '2026-07-01T10:00:00+00:00', NULL, "
        "'4.8.0-alpha', 'WORLD_CUP_V4.7.0_ALPHA', 0.7, 0.15, 0.15, ?, ?)",
        (json.dumps({"dc": {"home": 0.7}}), json.dumps([[0.2, 0.1], [0.3, 0.4]])),
    )
    conn.commit()
    conn.close()


def test_feature_snapshot_payload_excludes_result_labels(tmp_path):
    db_path = tmp_path / "features.db"
    _create_db(db_path)

    records = build_feature_snapshot_records(db_path)

    assert len(records) == 1
    payload = records[0]["payload"]
    assert payload["home_team"] == "Alpha"
    assert payload["schema_version"] == "feature_snapshot.v2"
    assert payload["current_probs"] == {"home": 0.7, "draw": 0.15, "away": 0.15}
    assert payload["canonical_result_source"] == "wc26_schedule"
    assert payload["current_prob_source"] == "pre_match_snapshots.final_probs"
    assert 0.0 <= payload["feature_quality_score"] <= 1.0
    assert isinstance(payload["quality_flags"], list)
    assert isinstance(payload["information_state_signals"], list)
    assert payload["information_state_signal_summary"]["shadow_only"] is True
    assert payload["information_state_v4_10"]["schema_version"] == "information_state_snapshot.v1"
    assert payload["information_state_v4_10"]["shadow_only"] is True
    assert payload["player_availability_shadow"]["source_status"]["shadow_only"] is True
    assert payload["schedule_context"]["source_status"]["shadow_only"] is True
    assert "actual_home_goals" not in payload
    assert "actual_away_goals" not in payload
    assert "home_goals" not in payload
    assert "away_goals" not in payload


def test_feature_snapshot_persistence_is_idempotent(tmp_path):
    db_path = tmp_path / "features.db"
    _create_db(db_path)
    records = build_feature_snapshot_records(db_path)

    first = persist_feature_snapshot_records(db_path, records)
    second = persist_feature_snapshot_records(db_path, records)

    assert first == {"inserted": 1, "skipped": 0, "total": 1}
    assert second == {"inserted": 0, "skipped": 1, "total": 1}
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM feature_snapshots").fetchone()[0] == 1
    finally:
        conn.close()
