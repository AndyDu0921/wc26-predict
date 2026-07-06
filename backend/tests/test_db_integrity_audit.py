import json
import sqlite3

from app.services.db_integrity_audit import audit_sqlite_integrity, repair_sqlite_foreign_key_drift


def _create_drift_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
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
            FOREIGN KEY(home_team_id) REFERENCES teams(id),
            FOREIGN KEY(away_team_id) REFERENCES teams(id)
        );
        CREATE TABLE news_signals (
            id TEXT PRIMARY KEY,
            team_id TEXT,
            match_id TEXT,
            payload TEXT,
            FOREIGN KEY(team_id) REFERENCES teams(id),
            FOREIGN KEY(match_id) REFERENCES matches(id)
        );
        """
    )
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO teams(id, name) VALUES ('t1', 'Alpha')")
    conn.execute("INSERT INTO matches(id, home_team_id, away_team_id) VALUES ('m1', 't1', 'missing-team')")
    conn.execute("INSERT INTO news_signals(id, team_id, match_id, payload) VALUES ('n1', '', '', 'empty refs')")
    conn.commit()
    conn.close()


def _create_alias_drift_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE teams (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE team_aliases (
            id TEXT PRIMARY KEY,
            team_id TEXT NOT NULL,
            alias TEXT NOT NULL,
            alias_normalized TEXT NOT NULL,
            source TEXT NOT NULL,
            FOREIGN KEY(team_id) REFERENCES teams(id)
        );
        """
    )
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO teams(id, name) VALUES ('new-brazil', 'Brazil')")
    conn.execute(
        "INSERT INTO team_aliases(id, team_id, alias, alias_normalized, source) "
        "VALUES ('alias-1', 'old-brazil', 'Brazil', 'brazil', 'openfootball')"
    )
    conn.commit()
    conn.close()


def test_integrity_audit_is_dry_run(tmp_path):
    db_path = tmp_path / "drift.db"
    _create_drift_db(db_path)

    payload = audit_sqlite_integrity(db_path)

    assert payload["schema_version"] == "db_integrity_audit.v1"
    assert payload["foreign_key_violation_count"] == 3
    actions = {item["action"] for item in payload["planned_actions"]}
    assert "normalize_empty_fk_to_null" in actions
    assert "quarantine_orphan_child_row" in actions

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT team_id, match_id FROM news_signals").fetchone() == ("", "")
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='data_integrity_quarantine'").fetchone()[0] == 0
    finally:
        conn.close()


def test_integrity_repair_normalizes_empty_nullable_fks_and_quarantines_orphans(tmp_path):
    db_path = tmp_path / "drift.db"
    _create_drift_db(db_path)

    payload = repair_sqlite_foreign_key_drift(db_path, backup=True)

    assert payload["schema_version"] == "db_integrity_repair.v1"
    assert payload["before_foreign_key_violation_count"] == 3
    assert payload["after_foreign_key_violation_count"] == 0
    assert payload["backup_path"]

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT team_id, match_id FROM news_signals").fetchone() == (None, None)
        assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0
        row = conn.execute("SELECT source_table, row_payload, violations FROM data_integrity_quarantine").fetchone()
    finally:
        conn.close()

    assert row[0] == "matches"
    assert json.loads(row[1])["away_team_id"] == "missing-team"
    assert json.loads(row[2])[0]["parent_table"] == "teams"


def test_integrity_repair_remaps_team_alias_when_name_match_is_exact(tmp_path):
    db_path = tmp_path / "alias_drift.db"
    _create_alias_drift_db(db_path)

    audit = audit_sqlite_integrity(db_path)
    assert audit["planned_actions"][0]["action"] == "repair_team_alias_id_by_exact_name"

    payload = repair_sqlite_foreign_key_drift(db_path, backup=False)

    assert payload["after_foreign_key_violation_count"] == 0
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT team_id FROM team_aliases WHERE id='alias-1'").fetchone()[0] == "new-brazil"
        assert conn.execute("SELECT COUNT(*) FROM data_integrity_quarantine").fetchone()[0] == 0
    finally:
        conn.close()
