"""SQLite integrity audit and conservative foreign-key repair helpers.

The repair mode is intentionally narrow:

* empty-string values in nullable FK columns are normalized to NULL;
* true orphan child rows are copied into an audit quarantine table and then
  removed from the child table;
* parent rows, prediction weights, model artifacts, and reports are never
  created or modified by this module.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


QUARANTINE_TABLE = "data_integrity_quarantine"


@dataclass(frozen=True)
class ForeignKeyViolation:
    table_name: str
    rowid: int | None
    parent_table: str
    fk_id: int
    child_columns: tuple[str, ...]
    parent_columns: tuple[str, ...]
    child_values: tuple[Any, ...]
    row_payload: dict[str, Any]

    def key(self) -> tuple[str, int | None]:
        return (self.table_name, self.rowid)

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_name": self.table_name,
            "rowid": self.rowid,
            "parent_table": self.parent_table,
            "fk_id": self.fk_id,
            "child_columns": list(self.child_columns),
            "parent_columns": list(self.parent_columns),
            "child_values": list(self.child_values),
            "row_payload": self.row_payload,
        }


def audit_sqlite_integrity(db_path: str | Path) -> dict[str, Any]:
    """Return an audit payload without changing the database."""
    path = Path(db_path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        violations = _foreign_key_violations(conn)
        row_actions = [_classify_row_action(conn, grouped) for grouped in _group_by_row(violations).values()]
        return {
            "schema_version": "db_integrity_audit.v1",
            "db_path": str(path),
            "generated_at": _now(),
            "integrity_check": integrity,
            "foreign_key_violation_count": len(violations),
            "affected_row_count": len(row_actions),
            "violation_counts_by_table": _counts(item.table_name for item in violations),
            "violation_counts_by_parent": _counts(item.parent_table for item in violations),
            "planned_actions": row_actions,
            "notes": (
                "Dry-run audit. Apply mode only normalizes nullable empty-string FKs "
                "or quarantines orphan child rows; it never fabricates parent rows."
            ),
        }
    finally:
        conn.close()


def repair_sqlite_foreign_key_drift(
    db_path: str | Path,
    *,
    backup: bool = True,
) -> dict[str, Any]:
    """Conservatively repair FK drift and return an auditable payload."""
    path = Path(db_path)
    backup_path = _backup_db(path) if backup else None
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        _ensure_quarantine_table(conn)
        before = _foreign_key_violations(conn)
        row_actions = [_classify_row_action(conn, grouped) for grouped in _group_by_row(before).values()]
        applied: list[dict[str, Any]] = []
        for action in row_actions:
            if action["action"] == "repair_team_alias_id_by_exact_name":
                _apply_team_alias_remap(conn, action)
                applied.append({**action, "applied": True})
            elif action["action"] == "normalize_empty_fk_to_null":
                _apply_null_normalization(conn, action)
                applied.append({**action, "applied": True})
            elif action["action"] == "quarantine_orphan_child_row":
                _apply_quarantine(conn, action)
                applied.append({**action, "applied": True})
            else:
                applied.append({**action, "applied": False})
        conn.commit()
        after = _foreign_key_violations(conn)
        return {
            "schema_version": "db_integrity_repair.v1",
            "db_path": str(path),
            "generated_at": _now(),
            "backup_path": str(backup_path) if backup_path else None,
            "before_foreign_key_violation_count": len(before),
            "after_foreign_key_violation_count": len(after),
            "applied_actions": applied,
            "remaining_violations": [item.to_dict() for item in after],
            "notes": (
                "Repair is conservative: empty nullable FKs were set to NULL; "
                "orphan child rows were preserved in data_integrity_quarantine."
            ),
        }
    finally:
        conn.close()


def _foreign_key_violations(conn: sqlite3.Connection) -> list[ForeignKeyViolation]:
    rows = conn.execute("PRAGMA foreign_key_check").fetchall()
    violations = []
    for row in rows:
        table_name = str(row[0])
        rowid = int(row[1]) if row[1] is not None else None
        parent_table = str(row[2])
        fk_id = int(row[3])
        fk_columns = _fk_columns(conn, table_name, fk_id)
        child_columns = tuple(item["from"] for item in fk_columns)
        parent_columns = tuple(item["to"] for item in fk_columns)
        payload = _row_payload(conn, table_name, rowid)
        child_values = tuple(payload.get(column) for column in child_columns)
        violations.append(
            ForeignKeyViolation(
                table_name=table_name,
                rowid=rowid,
                parent_table=parent_table,
                fk_id=fk_id,
                child_columns=child_columns,
                parent_columns=parent_columns,
                child_values=child_values,
                row_payload=payload,
            )
        )
    return violations


def _fk_columns(conn: sqlite3.Connection, table_name: str, fk_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(f"PRAGMA foreign_key_list({_quote_identifier(table_name)})").fetchall()
    selected = [dict(row) for row in rows if int(row["id"]) == fk_id]
    return sorted(selected, key=lambda item: int(item["seq"]))


def _row_payload(conn: sqlite3.Connection, table_name: str, rowid: int | None) -> dict[str, Any]:
    if rowid is None:
        return {}
    row = conn.execute(
        f"SELECT * FROM {_quote_identifier(table_name)} WHERE rowid=?",
        (rowid,),
    ).fetchone()
    return dict(row) if row is not None else {}


def _group_by_row(violations: list[ForeignKeyViolation]) -> dict[tuple[str, int | None], list[ForeignKeyViolation]]:
    grouped: dict[tuple[str, int | None], list[ForeignKeyViolation]] = defaultdict(list)
    for violation in violations:
        grouped[violation.key()].append(violation)
    return dict(grouped)


def _classify_row_action(conn: sqlite3.Connection, violations: list[ForeignKeyViolation]) -> dict[str, Any]:
    first = violations[0]
    team_alias_action = _team_alias_exact_name_action(conn, violations)
    if team_alias_action is not None:
        return team_alias_action
    nullable = _nullable_columns(conn, first.table_name)
    all_empty_nullable = all(
        _all_empty_nullable(violation, nullable)
        for violation in violations
    )
    violation_payload = [violation.to_dict() for violation in violations]
    if first.rowid is None:
        return {
            "action": "manual_review_without_rowid",
            "table_name": first.table_name,
            "rowid": None,
            "violations": violation_payload,
            "reason": "foreign_key_check returned no rowid",
        }
    if all_empty_nullable:
        columns = sorted({column for violation in violations for column in violation.child_columns})
        return {
            "action": "normalize_empty_fk_to_null",
            "table_name": first.table_name,
            "rowid": first.rowid,
            "columns": columns,
            "violations": violation_payload,
            "reason": "nullable FK column contains empty string instead of NULL",
        }
    return {
        "action": "quarantine_orphan_child_row",
        "table_name": first.table_name,
        "rowid": first.rowid,
        "violations": violation_payload,
        "reason": "child row references missing parent row; no safe parent can be fabricated",
    }


def _nullable_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({_quote_identifier(table_name)})").fetchall()
    return {str(row["name"]) for row in rows if int(row["notnull"]) == 0}


def _all_empty_nullable(violation: ForeignKeyViolation, nullable_columns: set[str]) -> bool:
    return bool(violation.child_columns) and all(
        column in nullable_columns and value == ""
        for column, value in zip(violation.child_columns, violation.child_values)
    )


def _team_alias_exact_name_action(
    conn: sqlite3.Connection,
    violations: list[ForeignKeyViolation],
) -> dict[str, Any] | None:
    if len(violations) != 1:
        return None
    violation = violations[0]
    if violation.table_name != "team_aliases" or violation.child_columns != ("team_id",):
        return None
    alias_normalized = str(violation.row_payload.get("alias_normalized") or "").strip().lower()
    if not alias_normalized:
        return None
    matches = conn.execute(
        """
        SELECT id, name
        FROM teams
        WHERE lower(trim(name))=?
        """,
        (alias_normalized,),
    ).fetchall()
    if len(matches) != 1:
        return None
    target = matches[0]
    return {
        "action": "repair_team_alias_id_by_exact_name",
        "table_name": violation.table_name,
        "rowid": violation.rowid,
        "columns": ["team_id"],
        "old_team_id": violation.child_values[0],
        "new_team_id": target["id"],
        "matched_team_name": target["name"],
        "violations": [violation.to_dict()],
        "reason": "team alias text exactly matches a single existing team name",
    }


def _apply_team_alias_remap(conn: sqlite3.Connection, action: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE team_aliases
        SET team_id=?
        WHERE rowid=?
        """,
        (action["new_team_id"], action["rowid"]),
    )


def _apply_null_normalization(conn: sqlite3.Connection, action: dict[str, Any]) -> None:
    assignments = ", ".join(f"{_quote_identifier(column)}=NULL" for column in action["columns"])
    conn.execute(
        f"UPDATE {_quote_identifier(action['table_name'])} SET {assignments} WHERE rowid=?",
        (action["rowid"],),
    )


def _apply_quarantine(conn: sqlite3.Connection, action: dict[str, Any]) -> None:
    table_name = str(action["table_name"])
    rowid = int(action["rowid"])
    row_payload = _row_payload(conn, table_name, rowid)
    conn.execute(
        f"""
        INSERT INTO {QUARANTINE_TABLE} (
            id, quarantined_at, source_table, source_rowid,
            reason, row_payload, violations
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            _now(),
            table_name,
            rowid,
            action["reason"],
            _json(row_payload),
            _json(action["violations"]),
        ),
    )
    conn.execute(
        f"DELETE FROM {_quote_identifier(table_name)} WHERE rowid=?",
        (rowid,),
    )


def _ensure_quarantine_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {QUARANTINE_TABLE} (
            id TEXT PRIMARY KEY,
            quarantined_at TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_rowid INTEGER,
            reason TEXT NOT NULL,
            row_payload TEXT NOT NULL,
            violations TEXT NOT NULL
        )
        """
    )


def _backup_db(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.with_name(f"{path.stem}.integrity-backup-{stamp}{path.suffix}")
    shutil.copy2(path, backup_path)
    return backup_path


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        text = str(value)
        counts[text] = counts.get(text, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
