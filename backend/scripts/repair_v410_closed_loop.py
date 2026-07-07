"""Repair V4.10 closed-loop gaps from already captured evidence.

This script is intentionally narrow and idempotent. It does not rerun
predictions, alter production weights, or promote model proposals.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_DB_PATH = BACKEND_DIR / "data" / "local_stage2.db"
DEFAULT_WEB_ODDS_CACHE = BACKEND_DIR / "data" / "_web_odds_cache.json"
DEFAULT_MATCH_IDS = ("194", "199", "200", "201")

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.closed_loop_feature_snapshot import (  # noqa: E402
    persist_feature_snapshot_from_latest_prematch,
)
from app.services.information_state_engine import (  # noqa: E402
    EvidenceInput,
    collect_match_evidence,
    extract_information_signals,
    score_information_signals,
    upsert_evidence_item,
)
from scripts.audit_match_closed_loop import audit_match_closed_loop  # noqa: E402
from scripts.backfill_prediction_persistence import repair_match  # noqa: E402


def repair_v410_closed_loop(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    match_ids: list[str] | None = None,
    persist: bool = False,
    web_odds_cache: str | Path = DEFAULT_WEB_ODDS_CACHE,
) -> dict[str, Any]:
    db_path = Path(db_path)
    match_ids = [str(item) for item in (match_ids or list(DEFAULT_MATCH_IDS))]
    backup_path = backup_db(db_path) if persist else None
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    actions: list[dict[str, Any]] = []
    try:
        before = _counts(conn, match_ids)
        with conn:
            for match_id in match_ids:
                actions.extend(_fix_known_kickoff(conn, match_id, persist=persist))
                actions.extend(_sync_prediction_report(conn, match_id, persist=persist))
                actions.extend(repair_match(conn, match_id, persist=persist))
                actions.extend(_repair_model_was_right(conn, match_id, persist=persist))
        if persist:
            for match_id in match_ids:
                actions.extend(_collect_snapshot_evidence(db_path, conn, match_id))
                actions.extend(_persist_feature_snapshot(db_path, match_id))
                actions.extend(_import_web_odds_cache(db_path, web_odds_cache, conn, match_id))
                actions.extend(_extract_and_score_signals(db_path, conn, match_id))
        after = _counts(conn, match_ids)
    finally:
        conn.close()

    pre_ids = [match_id for match_id in match_ids if match_id in {"194", "201"}]
    post_ids = [match_id for match_id in match_ids if match_id in {"199", "200"}]
    audits: dict[str, Any] = {}
    if pre_ids:
        audits["pre"] = audit_match_closed_loop(db_path, match_ids=pre_ids, phase="pre")
    if post_ids:
        audits["post"] = audit_match_closed_loop(db_path, match_ids=post_ids, phase="post")
    return {
        "schema_version": "v410_closed_loop_repair.v1",
        "mode": "persist" if persist else "dry_run",
        "db_path": str(db_path),
        "backup_path": str(backup_path) if backup_path else None,
        "match_ids": match_ids,
        "before": before,
        "actions": actions,
        "after": after,
        "audits": audits,
    }


def backup_db(db_path: Path) -> Path:
    backup_dir = REPO_ROOT / "_archive" / "db_backups" / "20260707"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{db_path.stem}.before-v410-closed-loop-repair-{stamp}{db_path.suffix}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def _counts(conn: sqlite3.Connection, match_ids: list[str]) -> dict[str, dict[str, int]]:
    tables = [
        "pre_match_snapshots",
        "prediction_snapshots",
        "prediction_runs",
        "feature_snapshots",
        "evidence_items",
        "information_state_signals",
        "signal_evaluations",
        "prediction_learning_log",
        "match_results",
        "match_team_statistics",
        "postmatch_process_eval",
    ]
    out: dict[str, dict[str, int]] = {}
    for match_id in match_ids:
        out[match_id] = {}
        for table in tables:
            out[match_id][table] = _count_direct(conn, table, match_id)
        out[match_id]["postmatch_eval"] = _count_postmatch_eval(conn, match_id)
    return out


def _fix_known_kickoff(
    conn: sqlite3.Connection,
    match_id: str,
    *,
    persist: bool,
) -> list[dict[str, Any]]:
    if match_id != "201":
        return []
    target = "2026-07-08T04:00:00"
    rows = conn.execute(
        """
        SELECT id, kickoff_at
        FROM pre_match_snapshots
        WHERE CAST(match_id AS TEXT) = ? AND kickoff_at <> ?
        """,
        (match_id, target),
    ).fetchall()
    if not rows:
        return []
    if persist:
        conn.execute(
            """
            UPDATE pre_match_snapshots
            SET kickoff_at = ?
            WHERE CAST(match_id AS TEXT) = ?
            """,
            (target, match_id),
        )
        conn.execute(
            """
            UPDATE prediction_snapshots
            SET match_time = ?
            WHERE CAST(match_id AS TEXT) = ?
            """,
            (target, match_id),
        )
    return [{
        "match_id": match_id,
        "action": "fix_kickoff_at",
        "old_values": [row["kickoff_at"] for row in rows],
        "new_value": target,
    }]


def _sync_prediction_report(
    conn: sqlite3.Connection,
    match_id: str,
    *,
    persist: bool,
) -> list[dict[str, Any]]:
    snapshot = _latest_pre_match_snapshot(conn, match_id)
    if snapshot is None:
        return []
    report_path = _report_for(snapshot["home_team"], snapshot["away_team"])
    if report_path is None:
        return [{
            "match_id": match_id,
            "action": "skip_report_markdown_sync",
            "reason": "report_file_missing",
        }]
    markdown = report_path.read_text(encoding="utf-8")
    corrected = _replace_generated_time(markdown, snapshot["snapshot_at"])
    if persist and corrected != markdown:
        report_path.write_text(corrected, encoding="utf-8")
    if persist:
        conn.execute(
            """
            UPDATE pre_match_snapshots
            SET report_markdown = ?
            WHERE id = ?
            """,
            (corrected, snapshot["id"]),
        )
        conn.execute(
            """
            UPDATE prediction_snapshots
            SET report_path = ?, report_markdown = ?
            WHERE CAST(match_id AS TEXT) = ?
            """,
            (str(report_path.relative_to(REPO_ROOT)).replace("\\", "/"), corrected, match_id),
        )
    return [{
        "match_id": match_id,
        "action": "sync_prediction_report_markdown",
        "report_path": str(report_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        "generated_time_source": "pre_match_snapshots.snapshot_at",
    }]


def _repair_model_was_right(
    conn: sqlite3.Connection,
    match_id: str,
    *,
    persist: bool,
) -> list[dict[str, Any]]:
    logs = conn.execute(
        """
        SELECT id, prediction_run_id, model_was_right
        FROM prediction_learning_log
        WHERE CAST(match_id AS TEXT) = ?
        """,
        (match_id,),
    ).fetchall()
    actions = []
    for log in logs:
        if log["model_was_right"] is not None:
            continue
        run = conn.execute(
            """
            SELECT home_win_prob, draw_prob, away_win_prob
            FROM prediction_runs
            WHERE id = ?
            """,
            (log["prediction_run_id"],),
        ).fetchone()
        result = conn.execute(
            """
            SELECT home_goals, away_goals
            FROM match_results
            WHERE CAST(match_id AS TEXT) = ?
            """,
            (match_id,),
        ).fetchone()
        if run is None or result is None:
            continue
        probs = [float(run["home_win_prob"]), float(run["draw_prob"]), float(run["away_win_prob"])]
        pred_idx = max(range(3), key=lambda idx: probs[idx])
        actual_idx = 0 if result["home_goals"] > result["away_goals"] else (1 if result["home_goals"] == result["away_goals"] else 2)
        value = int(pred_idx == actual_idx)
        if persist:
            conn.execute(
                """
                UPDATE prediction_learning_log
                SET model_was_right = ?
                WHERE id = ?
                """,
                (value, log["id"]),
            )
        actions.append({
            "match_id": match_id,
            "action": "update_model_was_right",
            "learning_log_id": log["id"],
            "model_was_right": bool(value),
        })
    return actions


def _collect_snapshot_evidence(
    db_path: Path,
    conn: sqlite3.Connection,
    match_id: str,
) -> list[dict[str, Any]]:
    snapshot = _latest_pre_match_snapshot(conn, match_id)
    if snapshot is None:
        return []
    result = collect_match_evidence(
        db_path,
        match_id=match_id,
        home_team=snapshot["home_team"],
        away_team=snapshot["away_team"],
    )
    return [{
        "match_id": match_id,
        "action": "collect_snapshot_evidence",
        "inserted": result.get("inserted", 0),
        "skipped": result.get("skipped", 0),
        "candidate_count": result.get("candidate_count", 0),
    }]


def _persist_feature_snapshot(db_path: Path, match_id: str) -> list[dict[str, Any]]:
    result = persist_feature_snapshot_from_latest_prematch(db_path, match_id=match_id)
    return [{"match_id": match_id, "action": "persist_feature_snapshot", **result}]


def _import_web_odds_cache(
    db_path: Path,
    cache_path: str | Path,
    conn: sqlite3.Connection,
    match_id: str,
) -> list[dict[str, Any]]:
    snapshot = _latest_pre_match_snapshot(conn, match_id)
    if snapshot is None:
        return []
    cache_file = Path(cache_path)
    if not cache_file.exists():
        return []
    cache = json.loads(cache_file.read_text(encoding="utf-8"))
    key = f"{snapshot['home_team']}|{snapshot['away_team']}"
    payload = cache.get(key)
    if not isinstance(payload, dict):
        return []
    fetched_at = payload.get("fetched_at") or payload.get("captured_at") or snapshot["snapshot_at"]
    evidence = EvidenceInput(
        evidence_type="market_odds",
        source_url=f"internal://web_odds_cache/{key}",
        source_name=str(payload.get("source") or "web_odds_cache"),
        title=f"Web odds consensus for {snapshot['home_team']} vs {snapshot['away_team']}",
        content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        language="en",
        published_at=fetched_at,
        fetched_at=fetched_at,
        available_at=fetched_at,
        reliability_score=0.78,
        match_id=match_id,
        home_team=snapshot["home_team"],
        away_team=snapshot["away_team"],
        metadata={
            "cache_key": key,
            "bookmakers": payload.get("bookmakers"),
            "sample_bookmakers": payload.get("sample_bookmakers"),
            "probabilities": {
                "home": payload.get("home_prob"),
                "draw": payload.get("draw_prob"),
                "away": payload.get("away_prob"),
            },
            "retroactive_probability_change": False,
        },
    )
    result = upsert_evidence_item(db_path, evidence)
    return [{
        "match_id": match_id,
        "action": "import_web_odds_cache_evidence",
        "cache_key": key,
        "inserted": result.get("inserted", False),
    }]


def _extract_and_score_signals(
    db_path: Path,
    conn: sqlite3.Connection,
    match_id: str,
) -> list[dict[str, Any]]:
    snapshot = _latest_pre_match_snapshot(conn, match_id)
    if snapshot is None:
        return []
    extracted = extract_information_signals(
        db_path,
        match_id=match_id,
        home_team=snapshot["home_team"],
        away_team=snapshot["away_team"],
        kickoff_at=snapshot["kickoff_at"],
        persist=True,
    )
    scored = score_information_signals(
        db_path,
        match_id=match_id,
        home_team=snapshot["home_team"],
        away_team=snapshot["away_team"],
    )
    return [{
        "match_id": match_id,
        "action": "extract_and_score_information_signals",
        "signals_extracted": extracted.get("signals_extracted", 0),
        "signals_scored": scored.get("signals_scored", 0),
    }]


def _latest_pre_match_snapshot(conn: sqlite3.Connection, match_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM pre_match_snapshots
        WHERE CAST(match_id AS TEXT) = ?
        ORDER BY snapshot_at DESC, id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()


def _report_for(home_team: str, away_team: str) -> Path | None:
    reports_dir = REPO_ROOT / "reports" / "predictions"
    candidates = sorted(
        reports_dir.glob(
            f"*_{home_team.replace(' ', '_')}_vs_{away_team.replace(' ', '_')}_prediction.md"
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _replace_generated_time(markdown: str, snapshot_at: str) -> str:
    lines = markdown.splitlines()
    for idx, line in enumerate(lines[:8]):
        if line.startswith("生成时间："):
            lines[idx] = f"生成时间：{snapshot_at}（DB赛前快照时间）"
            return "\n".join(lines) + ("\n" if markdown.endswith("\n") else "")
    return markdown


def _count_direct(conn: sqlite3.Connection, table_name: str, match_id: str) -> int:
    if not _has_table(conn, table_name):
        return 0
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}
    if "match_id" not in columns:
        return 0
    return int(
        conn.execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE CAST(match_id AS TEXT)=?",
            (match_id,),
        ).fetchone()[0]
    )


def _count_postmatch_eval(conn: sqlite3.Connection, match_id: str) -> int:
    if not (_has_table(conn, "postmatch_eval") and _has_table(conn, "prediction_runs")):
        return 0
    return int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM postmatch_eval pe
            JOIN prediction_runs pr ON pe.prediction_run_id = pr.id
            WHERE CAST(pr.match_id AS TEXT)=?
            """,
            (match_id,),
        ).fetchone()[0]
    )


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--match-ids", nargs="+", default=list(DEFAULT_MATCH_IDS))
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--web-odds-cache", default=str(DEFAULT_WEB_ODDS_CACHE))
    args = parser.parse_args(argv)

    result = repair_v410_closed_loop(
        args.db_path,
        match_ids=[str(item) for item in args.match_ids],
        persist=args.persist,
        web_odds_cache=args.web_odds_cache,
    )
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    audits = result.get("audits", {})
    return 0 if all(audit.get("passed", False) for audit in audits.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
