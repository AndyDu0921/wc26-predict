"""Audit one or more matches for V4.10 closed-loop completeness.

The audit is read-only.  It checks that a prediction or post-match run did not
quietly stop after writing a Markdown report while leaving required database
records missing.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any
from uuid import UUID


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_DB_PATH = BACKEND_DIR / "data" / "local_stage2.db"


PREMATCH_REQUIREMENTS = (
    "match_parent",
    "pre_match_snapshots",
    "prediction_snapshots",
    "prediction_runs",
    "feature_snapshots",
    "prediction_report_path",
    "evidence_items",
)

POSTMATCH_REQUIREMENTS = (
    "match_results",
    "match_team_statistics",
    "postmatch_process_eval",
    "prediction_learning_log",
    "postmatch_eval",
    "signal_evaluations",
    "postmatch_report",
    "postmatch_memory",
)


def audit_match_closed_loop(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    match_ids: list[str],
    phase: str = "all",
    repo_root: str | Path = REPO_ROOT,
) -> dict[str, Any]:
    db_path = Path(db_path)
    repo_root = Path(repo_root)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            _audit_one(conn, repo_root=repo_root, match_id=str(match_id), phase=phase)
            for match_id in match_ids
        ]
    finally:
        conn.close()

    failed = [row for row in rows if not row["passed"]]
    return {
        "schema_version": "match_closed_loop_audit.v1",
        "db_path": str(db_path),
        "phase": phase,
        "match_count": len(rows),
        "passed": not failed,
        "matches": rows,
        "failed_match_ids": [row["match_id"] for row in failed],
    }


def _audit_one(
    conn: sqlite3.Connection,
    *,
    repo_root: Path,
    match_id: str,
    phase: str,
) -> dict[str, Any]:
    context = _match_context(conn, match_id)
    counts = {
        "match_parent": _count_match_parent(conn, match_id),
        "pre_match_snapshots": _count_direct(conn, "pre_match_snapshots", match_id),
        "prediction_snapshots": _count_direct(conn, "prediction_snapshots", match_id),
        "prediction_runs": _count_direct(conn, "prediction_runs", match_id),
        "feature_snapshots": _count_direct(conn, "feature_snapshots", match_id),
        "evidence_items": _count_direct(conn, "evidence_items", match_id),
        "information_state_signals": _count_direct(conn, "information_state_signals", match_id),
        "signal_evaluations": _count_direct(conn, "signal_evaluations", match_id),
        "match_results": _count_direct(conn, "match_results", match_id),
        "match_team_statistics": _count_direct(conn, "match_team_statistics", match_id),
        "postmatch_process_eval": _count_direct(conn, "postmatch_process_eval", match_id),
        "prediction_learning_log": _count_direct(conn, "prediction_learning_log", match_id),
        "postmatch_eval": _count_postmatch_eval(conn, match_id),
    }
    report_path = _latest_prediction_report_path(conn, match_id)
    report_exists = _path_exists(repo_root, report_path)
    postmatch_report = _postmatch_report_exists(repo_root, context)
    postmatch_memory = _postmatch_memory_exists(repo_root, context)

    checks = {
        "match_parent": counts["match_parent"] > 0,
        "pre_match_snapshots": counts["pre_match_snapshots"] > 0,
        "prediction_snapshots": counts["prediction_snapshots"] > 0,
        "prediction_runs": counts["prediction_runs"] > 0,
        "feature_snapshots": counts["feature_snapshots"] > 0,
        "prediction_report_path": report_exists,
        "evidence_items": counts["evidence_items"] > 0,
        "match_results": counts["match_results"] > 0,
        "match_team_statistics": counts["match_team_statistics"] >= 2,
        "postmatch_process_eval": counts["postmatch_process_eval"] > 0,
        "prediction_learning_log": counts["prediction_learning_log"] > 0,
        "postmatch_eval": counts["postmatch_eval"] > 0,
        "signal_evaluations": counts["signal_evaluations"] > 0,
        "postmatch_report": postmatch_report["exists"],
        "postmatch_memory": postmatch_memory["exists"],
    }
    required = []
    if phase in {"pre", "all"}:
        required.extend(PREMATCH_REQUIREMENTS)
    if phase in {"post", "all"}:
        required.extend(POSTMATCH_REQUIREMENTS)
    missing = [name for name in required if not checks.get(name, False)]
    return {
        "match_id": match_id,
        "phase": phase,
        "passed": not missing,
        "missing": missing,
        "counts": counts,
        "checks": checks,
        "context": context,
        "prediction_report_path": report_path,
        "prediction_report_exists": report_exists,
        "postmatch_report": postmatch_report,
        "postmatch_memory": postmatch_memory,
    }


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _match_id_keys(match_id: str) -> list[str]:
    raw = str(match_id)
    keys = [raw]
    try:
        parsed = UUID(raw)
    except ValueError:
        return keys
    keys.extend([parsed.hex, str(parsed)])
    return list(dict.fromkeys(keys))


def _in_clause(match_id: str) -> tuple[str, list[str]]:
    keys = _match_id_keys(match_id)
    return ", ".join("?" for _ in keys), keys


def _count_direct(conn: sqlite3.Connection, table_name: str, match_id: str) -> int:
    if not _has_table(conn, table_name):
        return 0
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}
    if "match_id" not in columns:
        return 0
    placeholders, params = _in_clause(match_id)
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM {table_name} WHERE CAST(match_id AS TEXT) IN ({placeholders})",
        params,
    ).fetchone()
    return int(row["c"] if row else 0)


def _count_match_parent(conn: sqlite3.Connection, match_id: str) -> int:
    if not _has_table(conn, "matches"):
        return 0
    placeholders, params = _in_clause(match_id)
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM matches WHERE CAST(id AS TEXT) IN ({placeholders})",
        params,
    ).fetchone()
    return int(row["c"] if row else 0)


def _count_postmatch_eval(conn: sqlite3.Connection, match_id: str) -> int:
    if not (_has_table(conn, "postmatch_eval") and _has_table(conn, "prediction_runs")):
        return 0
    placeholders, params = _in_clause(match_id)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM postmatch_eval pe
        JOIN prediction_runs pr ON pe.prediction_run_id = pr.id
        WHERE CAST(pr.match_id AS TEXT) IN ({placeholders})
        """,
        params,
    ).fetchone()
    return int(row["c"] if row else 0)


def _latest_prediction_report_path(conn: sqlite3.Connection, match_id: str) -> str | None:
    if not _has_table(conn, "prediction_snapshots"):
        return None
    placeholders, params = _in_clause(match_id)
    row = conn.execute(
        f"""
        SELECT report_path
        FROM prediction_snapshots
        WHERE CAST(match_id AS TEXT) IN ({placeholders})
          AND report_path IS NOT NULL
          AND report_path <> ''
        ORDER BY generated_at DESC, id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    return str(row["report_path"]) if row and row["report_path"] else None


def _path_exists(repo_root: Path, rel_path: str | None) -> bool:
    if not rel_path:
        return False
    candidate = Path(rel_path)
    if candidate.is_absolute():
        return candidate.exists()
    return (repo_root / rel_path.replace("\\", "/")).exists()


def _match_context(conn: sqlite3.Connection, match_id: str) -> dict[str, str | None]:
    for table in ("pre_match_snapshots", "wc26_schedule"):
        if not _has_table(conn, table):
            continue
        placeholders, params = _in_clause(match_id)
        if table == "pre_match_snapshots":
            row = conn.execute(
                f"""
                SELECT home_team, away_team, kickoff_at
                FROM pre_match_snapshots
                WHERE CAST(match_id AS TEXT) IN ({placeholders})
                ORDER BY snapshot_at DESC, id DESC
                LIMIT 1
                """,
                params,
            ).fetchone()
        else:
            row = conn.execute(
                f"""
                SELECT home_team, away_team,
                       match_date || 'T' || kickoff_time || ':00' AS kickoff_at
                FROM wc26_schedule
                WHERE CAST(id AS TEXT) IN ({placeholders})
                LIMIT 1
                """,
                params,
            ).fetchone()
        if row is not None:
            return {
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "kickoff_at": row["kickoff_at"],
            }
    return {"home_team": None, "away_team": None, "kickoff_at": None}


def _postmatch_report_exists(repo_root: Path, context: dict[str, str | None]) -> dict[str, Any]:
    home = context.get("home_team")
    away = context.get("away_team")
    if not home or not away:
        return {"exists": False, "path": None}
    reports_dir = repo_root / "reports" / "postmatch"
    home_token = home.replace(" ", "_")
    away_token = away.replace(" ", "_")
    matches = sorted(reports_dir.glob(f"*_{home_token}_{away_token}_postmatch.md"))
    return {
        "exists": bool(matches),
        "path": str(matches[-1].relative_to(repo_root)).replace("\\", "/") if matches else None,
    }


def _postmatch_memory_exists(repo_root: Path, context: dict[str, str | None]) -> dict[str, Any]:
    home = context.get("home_team")
    away = context.get("away_team")
    if not home or not away:
        return {"exists": False, "path": None}
    memory_dir = repo_root / "memory"
    home_token = home.replace(" ", "")
    away_token = away.replace(" ", "")
    matches = sorted(memory_dir.glob(f"wc-postmatch-{home_token}-{away_token}-*.md"))
    return {
        "exists": bool(matches),
        "path": str(matches[-1].relative_to(repo_root)).replace("\\", "/") if matches else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--match-id", dest="match_ids", action="append", required=True)
    parser.add_argument("--phase", choices=("pre", "post", "all"), default="all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = audit_match_closed_loop(
        args.db_path,
        match_ids=args.match_ids,
        phase=args.phase,
    )
    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        for row in result["matches"]:
            status = "PASS" if row["passed"] else "FAIL"
            missing = ", ".join(row["missing"]) if row["missing"] else "none"
            print(f"{status} match={row['match_id']} phase={row['phase']} missing={missing}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
