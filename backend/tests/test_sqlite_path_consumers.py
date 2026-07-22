from __future__ import annotations

from pathlib import Path

from app.routers.analysis import _read_match_data
from app.services import group_standings, match_resolver


def test_group_standings_default_uses_configured_sqlite(monkeypatch, tmp_path):
    db_path = tmp_path / "standings.db"
    monkeypatch.setattr(
        group_standings,
        "current_sync_sqlite_path",
        lambda: db_path.resolve(),
    )

    service = group_standings.GroupStandingsService()

    assert Path(service._db_path) == db_path.resolve()


def test_match_resolver_default_uses_configured_sqlite(monkeypatch, tmp_path):
    db_path = tmp_path / "resolver.db"
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
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
            """
        )
    finally:
        conn.close()
    monkeypatch.setattr(
        match_resolver,
        "current_sync_sqlite_path",
        lambda: db_path.resolve(),
    )

    assert match_resolver.resolve_match_id(
        home_team="Alpha",
        away_team="Beta",
    ) is None


def test_analysis_reader_uses_explicit_database_path(tmp_path):
    db_path = tmp_path / "analysis.db"
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE teams (id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE matches (
                id TEXT PRIMARY KEY,
                home_team_id TEXT,
                away_team_id TEXT
            );
            """
        )
    finally:
        conn.close()

    assert _read_match_data("missing", db_path) is None
