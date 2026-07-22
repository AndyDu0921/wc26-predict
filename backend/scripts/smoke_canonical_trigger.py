#!/usr/bin/env python3
"""Smoke-test API/worker canonical prediction trigger on a temporary DB copy."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_SOURCE_DB = BACKEND_DIR / "data" / "local_stage2.db"
DEFAULT_WORK_DIR = BACKEND_DIR / "tmp" / "canonical_trigger_smoke"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run canonical trigger smoke on a copied SQLite DB")
    parser.add_argument("--source-db", default=str(DEFAULT_SOURCE_DB))
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    parser.add_argument("--keep", action="store_true", help="Keep temp directory for inspection")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--db-path", default="", help=argparse.SUPPRESS)
    parser.add_argument("--report-dir", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return _run_child(Path(args.db_path), Path(args.report_dir))
    return _run_parent(Path(args.source_db), Path(args.work_dir), keep=args.keep)


def _run_parent(source_db: Path, work_dir: Path, *, keep: bool) -> int:
    source_db = source_db.resolve()
    if not source_db.exists():
        raise SystemExit(f"source DB not found: {source_db}")
    before = _table_counts(source_db)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = (work_dir / stamp).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    temp_db = run_dir / "local_stage2.db"
    report_dir = run_dir / "reports" / "predictions"
    report_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_db, temp_db)

    env = os.environ.copy()
    env["POSTGRES_URL"] = f"sqlite+aiosqlite:///{temp_db.as_posix()}"
    env["PREDICTION_REPORT_DIR"] = str(report_dir)
    env["PYTHONPATH"] = str(BACKEND_DIR)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--child",
        "--db-path",
        str(temp_db),
        "--report-dir",
        str(report_dir),
    ]
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=180,
    )
    after = _table_counts(source_db)
    source_unchanged = before == after
    payload: dict[str, Any]
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {
            "status": "child_output_unparseable",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    payload["source_db_unchanged"] = source_unchanged
    payload["source_counts_before"] = before
    payload["source_counts_after"] = after
    payload["temp_db"] = str(temp_db)
    payload["report_dir"] = str(report_dir)
    payload["child_returncode"] = proc.returncode
    if proc.stderr:
        payload["child_stderr"] = proc.stderr
    print(json.dumps(payload, ensure_ascii=True, indent=2, default=str))
    if not keep and proc.returncode == 0:
        shutil.rmtree(run_dir, ignore_errors=True)
    return 0 if proc.returncode == 0 and source_unchanged and payload.get("passed") else 1


def _run_child(db_path: Path, report_dir: Path) -> int:
    async def _inner() -> dict[str, Any]:
        from app.database import AsyncSessionLocal
        from app.services.canonical_prediction_runner import run_canonical_prediction
        from scripts.audit_match_closed_loop import audit_match_closed_loop

        async with AsyncSessionLocal() as db:
            match = _select_future_match(db_path)
            inserted_synthetic = False
            if match is None:
                match = await _insert_synthetic_match(db)
                await db.commit()
                inserted_synthetic = True
            run_id = await run_canonical_prediction(match_id=match.id, run_type="manual", db=db)

        counts = _counts_for_match(db_path, str(match.id))
        run_type = _latest_run_type(db_path, str(match.id))
        audit = audit_match_closed_loop(db_path, match_ids=[str(match.id)], phase="pre")
        passed = (
            counts["prediction_runs"] > 0
            and counts["pre_match_snapshots"] > 0
            and counts["prediction_snapshots"] > 0
            and counts["feature_snapshots"] > 0
            and run_type == "manual"
            and audit.get("passed") is True
        )
        return {
            "passed": passed,
            "match_id": str(match.id),
            "run_id": str(run_id),
            "run_type": run_type,
            "inserted_synthetic_match": inserted_synthetic,
            "counts": counts,
            "closed_loop_audit": audit,
            "report_files": [str(path) for path in sorted(report_dir.glob("*.md"))],
        }

    try:
        payload = asyncio.run(_inner())
        print(json.dumps(payload, ensure_ascii=True, indent=2, default=str))
        return 0 if payload.get("passed") else 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "passed": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return 1


def _select_future_match(db_path: Path):
    now = datetime.now(timezone.utc)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='matches'").fetchone() is None:
            return None
        rows = conn.execute(
            """
            SELECT m.id, m.match_date, ht.name AS home_team, at.name AS away_team
            FROM matches m
            JOIN teams ht ON ht.id = m.home_team_id
            JOIN teams at ON at.id = m.away_team_id
            WHERE m.status = 'scheduled'
            ORDER BY m.match_date ASC
            """
        ).fetchall()
    for row in rows:
        try:
            match_id = UUID(str(row["id"]))
        except ValueError:
            continue
        match_date = _parse_dt(row["match_date"])
        if match_date is None or match_date >= now:
            home = str(row["home_team"] or "").strip().lower()
            away = str(row["away_team"] or "").strip().lower()
            if home == "tbd" or away == "tbd":
                continue
            return SimpleNamespace(id=match_id)
    return None


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _insert_synthetic_match(db):
    from sqlalchemy import select

    from app.models import Match, Team
    from app.models.enums import CompetitionType, MatchStatus

    teams_result = await db.execute(
        select(Team).where(Team.name.in_(["France", "Spain"])).order_by(Team.name.asc())
    )
    teams = teams_result.scalars().all()
    if len(teams) < 2:
        fallback = await db.execute(select(Team).order_by(Team.name.asc()).limit(2))
        teams = fallback.scalars().all()
    if len(teams) < 2:
        raise RuntimeError("Need at least two teams to create synthetic smoke match")
    home, away = teams[0], teams[1]
    match = Match(
        id=uuid4(),
        external_id=f"canonical-smoke:{uuid4().hex[:10]}",
        home_team_id=home.id,
        away_team_id=away.id,
        match_date=datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc),
        competition="FIFA World Cup 2026",
        competition_type=CompetitionType.NATIONAL,
        competition_weight=1.0,
        stage="Smoke",
        venue="Smoke Stadium",
        is_neutral_venue=True,
        status=MatchStatus.SCHEDULED,
    )
    db.add(match)
    await db.flush()
    match.home_team = home
    match.away_team = away
    return match


def _table_counts(db_path: Path) -> dict[str, int]:
    tables = [
        "prediction_runs",
        "pre_match_snapshots",
        "prediction_snapshots",
        "feature_snapshots",
    ]
    with sqlite3.connect(str(db_path)) as conn:
        return {table: _count_table(conn, table) for table in tables}


def _count_table(conn: sqlite3.Connection, table: str) -> int:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _counts_for_match(db_path: Path, match_id: str) -> dict[str, int]:
    tables = [
        "prediction_runs",
        "pre_match_snapshots",
        "prediction_snapshots",
        "feature_snapshots",
        "evidence_items",
    ]
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return {table: _count_match(conn, table, match_id) for table in tables}


def _count_match(conn: sqlite3.Connection, table: str, match_id: str) -> int:
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
        return 0
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if "match_id" not in columns:
        return 0
    placeholders, params = _match_id_clause(match_id)
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM {table} WHERE CAST(match_id AS TEXT) IN ({placeholders})",
        params,
    ).fetchone()
    return int(row["c"] if row else 0)


def _latest_run_type(db_path: Path, match_id: str) -> str | None:
    placeholders, params = _match_id_clause(match_id)
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            f"""
            SELECT run_type
            FROM prediction_runs
            WHERE CAST(match_id AS TEXT) IN ({placeholders})
            ORDER BY created_at DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    return str(row[0]) if row else None


def _match_id_clause(match_id: str) -> tuple[str, list[str]]:
    raw = str(match_id)
    keys = [raw]
    try:
        parsed = UUID(raw)
    except ValueError:
        pass
    else:
        keys.extend([parsed.hex, str(parsed)])
    keys = list(dict.fromkeys(keys))
    return ", ".join("?" for _ in keys), keys


if __name__ == "__main__":
    raise SystemExit(main())
