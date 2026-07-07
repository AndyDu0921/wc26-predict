import sqlite3

from app.services.accuracy_todo_backlog import build_accuracy_todo_backlog


def _create_backlog_db(path):
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
    conn.execute("INSERT INTO teams(id, name) VALUES ('h1', 'Alpha')")
    conn.execute("INSERT INTO teams(id, name) VALUES ('a1', 'Beta')")
    conn.execute(
        "INSERT INTO matches(id, home_team_id, away_team_id, match_date, competition, stage) "
        "VALUES ('m1', 'h1', 'a1', '2026-06-15T20:00:00+00:00', 'FIFA World Cup 2026', 'Group')"
    )
    conn.execute("INSERT INTO match_results(match_id, home_goals, away_goals) VALUES ('m1', 1, 0)")
    conn.execute(
        "INSERT INTO pre_match_snapshots(id, match_id, home_team, away_team, snapshot_at, kickoff_at, "
        "model_version, weight_config_label, final_home_prob, final_draw_prob, final_away_prob, "
        "component_probs, fused_score_matrix) VALUES ("
        "'p1', 'm1', 'Alpha', 'Beta', '2026-06-15T10:00:00+00:00', "
        "'2026-06-15T20:00:00+00:00', '4.9.0-alpha', 'WORLD_CUP_V4.9.0_ALPHA', "
        "0.6, 0.25, 0.15, '{\"dc\": {}}', '[[0.2, 0.1], [0.3, 0.4]]')"
    )
    conn.commit()
    conn.close()


def _table_counts(db_path):
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in sorted(tables)
        }
    finally:
        conn.close()


def test_accuracy_todo_backlog_is_read_only_and_prioritizes_sample_gap(tmp_path):
    db_path = tmp_path / "todo.db"
    _create_backlog_db(db_path)
    before_counts = _table_counts(db_path)

    payload = build_accuracy_todo_backlog(db_path, strict_sample_target=2)

    assert payload["schema_version"] == "accuracy_todo_backlog.v1"
    assert payload["summary"]["strict_sample_count"] == 1
    assert _table_counts(db_path) == before_counts

    items = {item["id"]: item for item in payload["items"]}
    assert items["P0-db-integrity-gate"]["status"] == "done"
    assert items["P0-strict-sample-gap"]["status"] == "open"
    assert items["P0-strict-sample-gap"]["evidence"]["gap"] == 1
    assert items["P1-market-snapshot-coverage"]["status"] == "open"
    assert items["P1-lineup-availability-coverage"]["status"] == "open"
    assert items["P2-prediction-pipeline-split"]["status"] == "open"


def test_accuracy_todo_backlog_keeps_odds_as_prediction_signal(tmp_path):
    db_path = tmp_path / "todo.db"
    _create_backlog_db(db_path)

    payload = build_accuracy_todo_backlog(db_path, strict_sample_target=1)
    market_item = [item for item in payload["items"] if item["id"] == "P1-market-snapshot-coverage"][0]

    assert market_item["category"] == "market_data"
    assert "odds" in market_item["next_action"]
    assert market_item["evidence"]["min_bookmakers"] == 3
