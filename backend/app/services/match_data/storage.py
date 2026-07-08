"""SQLite storage helpers for Match Data OS.

These helpers are deliberately small and explicit.  They support Alembic-managed
databases and temporary test databases without requiring the full SQLAlchemy app
stack.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.match_data.schema import (
    GameStateSegment,
    LineupPlayer,
    MatchEvent,
    NormalizedMatchData,
    PlayerMatchStats,
    RawOfficialMatchData,
    ShotEvent,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def stable_key(*parts: Any) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


DDL = """
CREATE TABLE IF NOT EXISTS match_data_raw (
    id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_match_id TEXT,
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    content_type TEXT,
    parser_version TEXT NOT NULL DEFAULT 'v4.11',
    status TEXT NOT NULL DEFAULT 'fetched',
    data_scope TEXT NOT NULL DEFAULT 'postmatch',
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(match_id, provider, payload_hash)
);

CREATE INDEX IF NOT EXISTS idx_match_data_raw_match ON match_data_raw(match_id);
CREATE INDEX IF NOT EXISTS idx_match_data_raw_provider ON match_data_raw(provider);
CREATE INDEX IF NOT EXISTS idx_match_data_raw_hash ON match_data_raw(payload_hash);

CREATE TABLE IF NOT EXISTS match_events (
    id TEXT PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    match_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_event_id TEXT,
    minute INTEGER,
    stoppage_minute INTEGER,
    period TEXT,
    team_name TEXT,
    side TEXT,
    player_name TEXT,
    related_player_name TEXT,
    event_type TEXT NOT NULL,
    outcome TEXT,
    xg REAL,
    home_score_after INTEGER,
    away_score_after INTEGER,
    payload_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_match_events_match ON match_events(match_id);
CREATE INDEX IF NOT EXISTS idx_match_events_type ON match_events(event_type);
CREATE INDEX IF NOT EXISTS idx_match_events_minute ON match_events(match_id, minute);

CREATE TABLE IF NOT EXISTS shot_events (
    id TEXT PRIMARY KEY,
    shot_key TEXT NOT NULL UNIQUE,
    event_id TEXT,
    match_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    minute INTEGER,
    stoppage_minute INTEGER,
    period TEXT,
    team_name TEXT,
    side TEXT,
    player_name TEXT,
    xg REAL,
    outcome TEXT,
    body_part TEXT,
    shot_type TEXT,
    assist_player TEXT,
    home_score_after INTEGER,
    away_score_after INTEGER,
    payload_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_shot_events_match ON shot_events(match_id);
CREATE INDEX IF NOT EXISTS idx_shot_events_minute ON shot_events(match_id, minute);

CREATE TABLE IF NOT EXISTS match_lineups (
    id TEXT PRIMARY KEY,
    lineup_key TEXT NOT NULL UNIQUE,
    match_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    team_name TEXT,
    side TEXT,
    player_id TEXT,
    player_name TEXT NOT NULL,
    position TEXT,
    shirt_number INTEGER,
    is_starting INTEGER,
    is_captain INTEGER,
    is_goalkeeper INTEGER,
    minute_on INTEGER,
    minute_off INTEGER,
    minutes_played INTEGER,
    payload_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_match_lineups_match ON match_lineups(match_id);
CREATE INDEX IF NOT EXISTS idx_match_lineups_player ON match_lineups(player_name);

CREATE TABLE IF NOT EXISTS player_match_minutes (
    id TEXT PRIMARY KEY,
    minute_key TEXT NOT NULL UNIQUE,
    match_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    team_name TEXT,
    side TEXT,
    player_id TEXT,
    player_name TEXT NOT NULL,
    minute_on INTEGER,
    minute_off INTEGER,
    minutes_played INTEGER,
    source_lineup_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_player_match_minutes_match ON player_match_minutes(match_id);

CREATE TABLE IF NOT EXISTS match_player_statistics (
    id TEXT PRIMARY KEY,
    stats_key TEXT NOT NULL UNIQUE,
    match_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    team_name TEXT,
    side TEXT,
    player_id TEXT,
    player_name TEXT NOT NULL,
    minutes_played INTEGER,
    goals INTEGER,
    assists INTEGER,
    shots INTEGER,
    xg REAL,
    passes_attempted INTEGER,
    pass_accuracy_pct REAL,
    tackles INTEGER,
    saves INTEGER,
    stats_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_match_player_statistics_match ON match_player_statistics(match_id);

CREATE TABLE IF NOT EXISTS match_game_state_segments (
    id TEXT PRIMARY KEY,
    segment_key TEXT NOT NULL UNIQUE,
    match_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'derived',
    period TEXT,
    minute_start INTEGER NOT NULL,
    minute_end INTEGER NOT NULL,
    home_score_start INTEGER NOT NULL DEFAULT 0,
    away_score_start INTEGER NOT NULL DEFAULT 0,
    home_score_end INTEGER NOT NULL DEFAULT 0,
    away_score_end INTEGER NOT NULL DEFAULT 0,
    leader_start TEXT,
    leader_end TEXT,
    home_events_count INTEGER NOT NULL DEFAULT 0,
    away_events_count INTEGER NOT NULL DEFAULT 0,
    home_shots INTEGER NOT NULL DEFAULT 0,
    away_shots INTEGER NOT NULL DEFAULT 0,
    home_xg REAL,
    away_xg REAL,
    cards_count INTEGER NOT NULL DEFAULT 0,
    substitutions_count INTEGER NOT NULL DEFAULT 0,
    state_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_match_game_state_segments_match ON match_game_state_segments(match_id);
"""


def ensure_match_data_os_tables(db_path: str | Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(DDL)
        conn.commit()


def save_raw_match_data(db_path: str | Path, raw: RawOfficialMatchData) -> dict[str, Any]:
    ensure_match_data_os_tables(db_path)
    payload_json = canonical_json(raw.payload)
    hsh = raw.payload_hash or hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    fetched_at = raw.fetched_at or utc_now_iso()
    row_id = stable_key(raw.match_id, raw.provider, hsh)
    with connect(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM match_data_raw WHERE match_id=? AND provider=? AND payload_hash=?",
            (raw.match_id, raw.provider, hsh),
        ).fetchone()
        if existing:
            return {"action": "existing", "id": existing["id"], "payload_hash": hsh}
        conn.execute(
            """
            INSERT INTO match_data_raw (
                id, match_id, provider, provider_match_id, source_url, fetched_at,
                payload_json, payload_hash, content_type, parser_version, status,
                data_scope, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_id,
                raw.match_id,
                raw.provider,
                raw.provider_match_id,
                raw.source_url,
                fetched_at,
                payload_json,
                hsh,
                raw.content_type,
                raw.parser_version,
                raw.status,
                raw.data_scope,
                raw.notes,
            ),
        )
        conn.commit()
    return {"action": "inserted", "id": row_id, "payload_hash": hsh}


def load_latest_raw_payload(
    db_path: str | Path,
    match_id: str,
    provider: str | None = None,
) -> dict[str, Any] | None:
    ensure_match_data_os_tables(db_path)
    sql = "SELECT * FROM match_data_raw WHERE match_id=?"
    params: list[Any] = [str(match_id)]
    if provider:
        sql += " AND provider=?"
        params.append(provider)
    sql += " ORDER BY fetched_at DESC, created_at DESC LIMIT 1"
    with connect(db_path) as conn:
        row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["payload"] = json.loads(result.pop("payload_json"))
    return result


def save_normalized_match_data(
    db_path: str | Path,
    data: NormalizedMatchData,
    *,
    replace_existing: bool = True,
) -> dict[str, int]:
    ensure_match_data_os_tables(db_path)
    with connect(db_path) as conn:
        if replace_existing:
            for table in (
                "match_events",
                "shot_events",
                "match_lineups",
                "player_match_minutes",
                "match_player_statistics",
            ):
                conn.execute(
                    f"DELETE FROM {table} WHERE match_id=? AND provider=?",
                    (data.match_id, data.provider),
                )
        event_count = 0
        shot_count = 0
        lineup_count = 0
        player_stat_count = 0

        for event in data.events:
            _insert_event(conn, event)
            event_count += 1
        for shot in data.shots:
            _insert_shot(conn, shot)
            shot_count += 1
        for player in data.lineups:
            lineup_id = _insert_lineup(conn, player)
            _insert_player_minutes(conn, player, lineup_id)
            lineup_count += 1
        for stats in data.player_stats:
            _insert_player_stats(conn, stats)
            player_stat_count += 1
        conn.commit()
    return {
        "events": event_count,
        "shots": shot_count,
        "lineups": lineup_count,
        "player_stats": player_stat_count,
    }


def save_game_state_segments(
    db_path: str | Path,
    match_id: str,
    segments: list[GameStateSegment],
    *,
    replace_existing: bool = True,
) -> int:
    ensure_match_data_os_tables(db_path)
    with connect(db_path) as conn:
        if replace_existing:
            conn.execute("DELETE FROM match_game_state_segments WHERE match_id=?", (str(match_id),))
        for segment in segments:
            segment_key = stable_key(
                segment.match_id,
                segment.provider,
                segment.minute_start,
                segment.minute_end,
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO match_game_state_segments (
                    id, segment_key, match_id, provider, period, minute_start, minute_end,
                    home_score_start, away_score_start, home_score_end, away_score_end,
                    leader_start, leader_end, home_events_count, away_events_count,
                    home_shots, away_shots, home_xg, away_xg, cards_count,
                    substitutions_count, state_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stable_key(segment_key, "row"),
                    segment_key,
                    str(segment.match_id),
                    segment.provider,
                    segment.period,
                    segment.minute_start,
                    segment.minute_end,
                    segment.home_score_start,
                    segment.away_score_start,
                    segment.home_score_end,
                    segment.away_score_end,
                    segment.leader_start,
                    segment.leader_end,
                    segment.home_events_count,
                    segment.away_events_count,
                    segment.home_shots,
                    segment.away_shots,
                    segment.home_xg,
                    segment.away_xg,
                    segment.cards_count,
                    segment.substitutions_count,
                    canonical_json(segment.state),
                ),
            )
        conn.commit()
    return len(segments)


def count_rich_match_data(db_path: str | Path, match_id: str) -> dict[str, int]:
    ensure_match_data_os_tables(db_path)
    tables = {
        "raw": "match_data_raw",
        "events": "match_events",
        "shots": "shot_events",
        "lineups": "match_lineups",
        "player_minutes": "player_match_minutes",
        "player_stats": "match_player_statistics",
        "segments": "match_game_state_segments",
    }
    with connect(db_path) as conn:
        return {
            label: int(
                conn.execute(f"SELECT COUNT(*) FROM {table} WHERE match_id=?", (str(match_id),)).fetchone()[0]
            )
            for label, table in tables.items()
        }


def _insert_event(conn: sqlite3.Connection, event: MatchEvent) -> str:
    event_key = stable_key(
        event.match_id,
        event.provider,
        event.provider_event_id,
        event.minute,
        event.stoppage_minute,
        event.event_type,
        event.team_name,
        event.player_name,
        canonical_json(event.payload),
    )
    row_id = stable_key(event_key, "event")
    conn.execute(
        """
        INSERT OR REPLACE INTO match_events (
            id, event_key, match_id, provider, provider_event_id, minute,
            stoppage_minute, period, team_name, side, player_name,
            related_player_name, event_type, outcome, xg, home_score_after,
            away_score_after, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            event_key,
            event.match_id,
            event.provider,
            event.provider_event_id,
            event.minute,
            event.stoppage_minute,
            event.period,
            event.team_name,
            event.side,
            event.player_name,
            event.related_player_name,
            event.event_type,
            event.outcome,
            event.xg,
            event.home_score_after,
            event.away_score_after,
            canonical_json(event.payload),
        ),
    )
    return row_id


def _insert_shot(conn: sqlite3.Connection, shot: ShotEvent) -> str:
    shot_key = stable_key(
        shot.match_id,
        shot.provider,
        shot.event_id,
        shot.minute,
        shot.player_name,
        shot.xg,
        shot.outcome,
        canonical_json(shot.payload),
    )
    row_id = stable_key(shot_key, "shot")
    conn.execute(
        """
        INSERT OR REPLACE INTO shot_events (
            id, shot_key, event_id, match_id, provider, minute, stoppage_minute,
            period, team_name, side, player_name, xg, outcome, body_part,
            shot_type, assist_player, home_score_after, away_score_after, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            shot_key,
            shot.event_id,
            shot.match_id,
            shot.provider,
            shot.minute,
            shot.stoppage_minute,
            shot.period,
            shot.team_name,
            shot.side,
            shot.player_name,
            shot.xg,
            shot.outcome,
            shot.body_part,
            shot.shot_type,
            shot.assist_player,
            shot.home_score_after,
            shot.away_score_after,
            canonical_json(shot.payload),
        ),
    )
    return row_id


def _insert_lineup(conn: sqlite3.Connection, player: LineupPlayer) -> str:
    lineup_key = stable_key(
        player.match_id,
        player.provider,
        player.team_name,
        player.player_id,
        player.player_name,
        player.minute_on,
        player.minute_off,
    )
    row_id = stable_key(lineup_key, "lineup")
    conn.execute(
        """
        INSERT OR REPLACE INTO match_lineups (
            id, lineup_key, match_id, provider, team_name, side, player_id,
            player_name, position, shirt_number, is_starting, is_captain,
            is_goalkeeper, minute_on, minute_off, minutes_played, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            lineup_key,
            player.match_id,
            player.provider,
            player.team_name,
            player.side,
            player.player_id,
            player.player_name,
            player.position,
            player.shirt_number,
            _bool_to_int(player.is_starting),
            _bool_to_int(player.is_captain),
            _bool_to_int(player.is_goalkeeper),
            player.minute_on,
            player.minute_off,
            player.minutes_played,
            canonical_json(player.payload),
        ),
    )
    return row_id


def _insert_player_minutes(
    conn: sqlite3.Connection,
    player: LineupPlayer,
    lineup_id: str,
) -> None:
    minute_key = stable_key(
        player.match_id,
        player.provider,
        player.team_name,
        player.player_id,
        player.player_name,
        "minutes",
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO player_match_minutes (
            id, minute_key, match_id, provider, team_name, side, player_id,
            player_name, minute_on, minute_off, minutes_played, source_lineup_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stable_key(minute_key, "row"),
            minute_key,
            player.match_id,
            player.provider,
            player.team_name,
            player.side,
            player.player_id,
            player.player_name,
            player.minute_on,
            player.minute_off,
            player.minutes_played,
            lineup_id,
        ),
    )


def _insert_player_stats(conn: sqlite3.Connection, stats: PlayerMatchStats) -> str:
    stats_key = stable_key(
        stats.match_id,
        stats.provider,
        stats.team_name,
        stats.player_id,
        stats.player_name,
        canonical_json(stats.stats),
    )
    row_id = stable_key(stats_key, "player_stats")
    conn.execute(
        """
        INSERT OR REPLACE INTO match_player_statistics (
            id, stats_key, match_id, provider, team_name, side, player_id,
            player_name, minutes_played, goals, assists, shots, xg,
            passes_attempted, pass_accuracy_pct, tackles, saves, stats_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row_id,
            stats_key,
            stats.match_id,
            stats.provider,
            stats.team_name,
            stats.side,
            stats.player_id,
            stats.player_name,
            stats.minutes_played,
            stats.goals,
            stats.assists,
            stats.shots,
            stats.xg,
            stats.passes_attempted,
            stats.pass_accuracy_pct,
            stats.tackles,
            stats.saves,
            canonical_json(stats.stats),
        ),
    )
    return row_id


def _bool_to_int(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0
