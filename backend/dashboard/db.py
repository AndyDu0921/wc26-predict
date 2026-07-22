"""db.py — Read-only SQLite connection helper for the Streamlit Dashboard.

All connections open with URI mode=ro for true read-only access at the
filesystem level, plus WAL mode for concurrent-read safety.

WARNING: This module MUST NOT provide any write capability.
All data writes remain through dedicated CLI scripts.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# ── Database path ────────────────────────────────────────────────────────────
BACKEND_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BACKEND_DIR / "data" / "local_stage2.db"

# ── Read-only validation ─────────────────────────────────────────────────────

_FORBIDDEN_SQL = re.compile(
    r"^\s*(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|ATTACH|DETACH|"
    r"REINDEX|REPLACE|VACUUM|PRAGMA\s+(?!journal_mode|query_only)|GRANT|REVOKE)",
    re.IGNORECASE | re.MULTILINE,
)


def _validate_read_only(sql: str) -> None:
    """Raise ValueError if SQL contains forbidden DML/DDL statements.

    Catches multi-statement injection patterns like:
        SELECT 1; DROP TABLE matches --
    """
    if _FORBIDDEN_SQL.search(sql):
        raise ValueError(
            "Only SELECT queries (and safe PRAGMA) are allowed in the Dashboard. "
            "Use CLI scripts for write operations."
        )


# ── Database helper ──────────────────────────────────────────────────────────


class DashboardDB:
    """Read-only SQLite connection manager for the Streamlit Dashboard.

    Uses URI-based ?mode=ro for filesystem-level read-only protection,
    plus WAL mode for concurrent read access during script writes.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DB_PATH

    def connect(self) -> sqlite3.Connection:
        """Open a read-only connection with WAL mode.

        Raises FileNotFoundError if the database does not exist.
        """
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {self.db_path}")
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    # ── Schema discovery ─────────────────────────────────────────────────

    def get_tables(self) -> list[str]:
        """Return sorted list of all user table names."""
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            ).fetchall()
            return [r["name"] for r in rows]
        finally:
            conn.close()

    def _validate_table(self, table: str) -> str:
        """Return the table name if it exists in the database, otherwise raise ValueError."""
        valid = self.get_tables()
        if table not in valid:
            raise ValueError(f"Unknown table: {table}")
        return table

    def get_table_info(self, table: str) -> list[dict[str, Any]]:
        """Return column metadata for a table.

        Each dict: name, type, notnull, pk (primary key).
        """
        safe_table = self._validate_table(table)
        conn = self.connect()
        try:
            rows = conn.execute(f"PRAGMA table_info('{safe_table}')").fetchall()
            return [
                {
                    "name": r["name"],
                    "type": r["type"],
                    "notnull": bool(r["notnull"]),
                    "pk": bool(r["pk"]),
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_row_count(self, table: str) -> int:
        """Return row count for a table."""
        safe_table = self._validate_table(table)
        conn = self.connect()
        try:
            row = conn.execute(
                f'SELECT COUNT(*) AS cnt FROM "{safe_table}"'
            ).fetchone()
            return row["cnt"] if row else 0
        finally:
            conn.close()

    # ── Query ────────────────────────────────────────────────────────────

    def query(
        self, sql: str, params: tuple = (), *, as_df: bool = False
    ) -> list[sqlite3.Row] | pd.DataFrame:
        """Execute a SELECT-only query with safety validation.

        Args:
            sql: SQL SELECT statement.
            params: Query parameters (safe substitution).
            as_df: Return pandas DataFrame instead of Row list.

        Returns:
            List of sqlite3.Row objects, or DataFrame if as_df=True.

        Raises:
            ValueError if the SQL contains forbidden DML/DDL.
        """
        _validate_read_only(sql)
        conn = self.connect()
        try:
            if as_df:
                return pd.read_sql_query(sql, conn, params=params)
            cursor = conn.execute(sql, params)
            return cursor.fetchall()
        finally:
            conn.close()

    # ── Domain helpers ───────────────────────────────────────────────────

    def get_teams(self) -> list[str]:
        """Return all team names, sorted alphabetically."""
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT name FROM teams ORDER BY name"
            ).fetchall()
            return [r["name"] for r in rows]
        finally:
            conn.close()

    def get_wc26_schedule(
        self, group: str | None = None
    ) -> list[dict[str, Any]]:
        """Return WC26 match schedule, optionally filtered by group.

        Each dict: match_number, group_name, home_team, away_team,
                   stage, match_date, venue, city, status.
        """
        conn = self.connect()
        try:
            if group:
                rows = conn.execute(
                    "SELECT * FROM wc26_schedule WHERE group_name = ? "
                    "ORDER BY match_number",
                    (group,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM wc26_schedule ORDER BY match_number"
                ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_wc26_groups(self) -> list[dict[str, Any]]:
        """Return all WC26 group assignments."""
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT * FROM wc26_groups ORDER BY group_name, slot"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_manual_events(
        self,
        team: str | None = None,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Return manual context events, optionally filtered by team.

        Args:
            team: Optional team name filter.
            active_only: If True (default), only return events where
                status='active' AND (expires_at IS NULL OR expires_at > now).
                Expired/pending events are hidden.
        """
        conn = self.connect()
        try:
            if team:
                if active_only:
                    rows = conn.execute(
                        "SELECT * FROM manual_events "
                        "WHERE team_name = ? "
                        "  AND status = 'active' "
                        "  AND (expires_at IS NULL OR expires_at > datetime('now')) "
                        "ORDER BY created_at DESC",
                        (team,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM manual_events WHERE team_name = ? "
                        "ORDER BY created_at DESC",
                        (team,),
                    ).fetchall()
            else:
                if active_only:
                    rows = conn.execute(
                        "SELECT * FROM manual_events "
                        "WHERE status = 'active' "
                        "  AND (expires_at IS NULL OR expires_at > datetime('now')) "
                        "ORDER BY created_at DESC "
                        "LIMIT 200"
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM manual_events ORDER BY created_at DESC "
                        "LIMIT 200"
                    ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_competitions(self) -> list[str]:
        """Return distinct competition names, sorted."""
        conn = self.connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT competition FROM matches "
                "WHERE competition IS NOT NULL ORDER BY competition"
            ).fetchall()
            return [r["competition"] for r in rows]
        finally:
            conn.close()

    def get_db_stats(self) -> dict[str, Any]:
        """Return summary statistics for the Overview page."""
        stats: dict[str, Any] = {}
        key_tables = [
            "matches", "teams", "players", "match_results",
            "prediction_snapshots", "news_signals", "news_articles",
            "manual_events", "wc26_groups", "wc26_schedule",
            "wc26_group_standings", "wc26_knockout_paths",
            "wc26_third_place_ranking",
        ]
        for table in key_tables:
            try:
                stats[table] = self.get_row_count(table)
            except (ValueError, Exception) as exc:
                logger.warning("Failed to get row count for table=%s: %s", table, exc)
                stats[table] = None
        stats["total_tables"] = len(self.get_tables())
        return stats


# ── Convenience singleton ────────────────────────────────────────────────────
db = DashboardDB()
