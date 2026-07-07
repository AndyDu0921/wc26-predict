"""Build a deterministic V4.9 accuracy-improvement todo backlog.

The backlog is read-only.  It translates local registry/database facts into
prioritized work items, so the next accuracy iteration starts from evidence
rather than memory.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.db_integrity_audit import audit_sqlite_integrity
from app.services.evaluation_registry import WC26_COMPETITION, build_evaluation_registry
from app.services.evaluation_registry_repair import build_evaluation_registry_repair_report


STRICT_SAMPLE_TARGET = 50
MIN_MARKET_BOOKMAKERS = 3
PIPELINE_LINE_TARGET = 500


def build_accuracy_todo_backlog(
    db_path: str | Path,
    *,
    competition: str = WC26_COMPETITION,
    strict_sample_target: int = STRICT_SAMPLE_TARGET,
) -> dict[str, Any]:
    """Build a read-only backlog for accuracy and technical-debt work."""
    registry = build_evaluation_registry(db_path, competition=competition)
    repair_report = build_evaluation_registry_repair_report(db_path, competition=competition)
    db_audit = audit_sqlite_integrity(db_path)
    snapshot_quality = _snapshot_quality(db_path, registry["samples"])
    code_quality = _code_quality()

    items = []
    items.extend(_db_items(db_audit))
    items.extend(_registry_items(registry, repair_report, strict_sample_target))
    items.extend(_snapshot_items(snapshot_quality))
    items.extend(_code_items(code_quality))

    status_counts = Counter(item["status"] for item in items)
    priority_counts = Counter(item["priority"] for item in items)
    return {
        "schema_version": "accuracy_todo_backlog.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "competition": competition,
        "registry_hash": registry["registry_hash"],
        "summary": {
            "total_items": len(items),
            "open_items": sum(1 for item in items if item["status"] == "open"),
            "blocked_items": sum(1 for item in items if item["status"] == "blocked"),
            "done_items": sum(1 for item in items if item["status"] == "done"),
            "status_counts": dict(status_counts),
            "priority_counts": dict(priority_counts),
            "strict_sample_target": strict_sample_target,
            "strict_sample_count": registry["summary"].get("strict_count"),
        },
        "evidence": {
            "registry_summary": registry["summary"],
            "repair_summary": repair_report["repair_summary"],
            "db_integrity": {
                "integrity_check": db_audit["integrity_check"],
                "foreign_key_violation_count": db_audit["foreign_key_violation_count"],
                "affected_row_count": db_audit["affected_row_count"],
            },
            "snapshot_quality": snapshot_quality,
            "code_quality": code_quality,
        },
        "items": items,
        "notes": (
            "Read-only backlog. It does not create snapshots, probabilities, "
            "weights, model artifacts, or reports."
        ),
    }


def _db_items(db_audit: dict[str, Any]) -> list[dict[str, Any]]:
    if db_audit["integrity_check"] == "ok" and db_audit["foreign_key_violation_count"] == 0:
        return [
            _item(
                item_id="P0-db-integrity-gate",
                priority="P0",
                category="data_integrity",
                title="Keep DB integrity gate green before every experiment",
                status="done",
                evidence={
                    "integrity_check": db_audit["integrity_check"],
                    "foreign_key_violation_count": db_audit["foreign_key_violation_count"],
                },
                next_action="Run audit_db_integrity.py before prediction tournaments and postmatch learning.",
            )
        ]
    return [
        _item(
            item_id="P0-db-integrity-repair",
            priority="P0",
            category="data_integrity",
            title="Repair SQLite foreign-key drift before model experiments",
            status="open",
            evidence={
                "integrity_check": db_audit["integrity_check"],
                "foreign_key_violation_count": db_audit["foreign_key_violation_count"],
                "violation_counts_by_table": db_audit.get("violation_counts_by_table"),
            },
            next_action="Run audit_db_integrity.py --apply after reviewing planned actions and keeping backup/quarantine evidence.",
        )
    ]


def _registry_items(
    registry: dict[str, Any],
    repair_report: dict[str, Any],
    strict_sample_target: int,
) -> list[dict[str, Any]]:
    summary = registry["summary"]
    repair = repair_report["repair_summary"]
    strict_count = int(summary.get("strict_count", 0) or 0)
    items = []
    if strict_count < strict_sample_target:
        items.append(
            _item(
                item_id="P0-strict-sample-gap",
                priority="P0",
                category="evaluation_registry",
                title="Increase strict no-leakage samples before promoting candidates",
                status="open",
                evidence={
                    "strict_count": strict_count,
                    "target": strict_sample_target,
                    "gap": strict_sample_target - strict_count,
                    "diagnostic_count": summary.get("diagnostic_count"),
                    "rejected_count": summary.get("rejected_count"),
                },
                next_action=(
                    "For diagnostic rows, import only real pre-kickoff snapshots/current probabilities "
                    "with verifiable kickoff_at and as_of_time. Do not create placeholder probabilities."
                ),
            )
        )
    if int(summary.get("source_result_conflicts", 0) or 0) > 0:
        items.append(
            _item(
                item_id="P0-result-source-conflict",
                priority="P0",
                category="evaluation_registry",
                title="Resolve canonical result source conflicts",
                status="blocked",
                evidence={"source_result_conflicts": summary.get("source_result_conflicts")},
                next_action="Manually verify final score from trusted result sources, then update only the conflicting source row with evidence.",
            )
        )

    actions = repair.get("action_counts") or {}
    for action, count in sorted(actions.items()):
        priority = "P0" if action in {
            "import_real_pre_match_snapshot",
            "recover_current_probabilities_from_valid_snapshot",
            "normalize_snapshot_and_kickoff_time",
        } else "P1"
        status = "open" if priority == "P0" else "blocked"
        items.append(
            _item(
                item_id=f"{priority}-registry-{action}",
                priority=priority,
                category="evaluation_registry",
                title=f"Registry repair action: {action}",
                status=status,
                evidence={"affected_samples": count},
                next_action="Use build_evaluation_registry_repair_report.py to inspect affected sample IDs and required evidence.",
            )
        )
    process_gap = int(summary.get("strict_count", 0) or 0) - int(summary.get("with_process_eval", 0) or 0)
    if process_gap > 0:
        items.append(
            _item(
                item_id="P1-process-eval-coverage",
                priority="P1",
                category="postmatch_learning",
                title="Backfill process evaluation coverage for strict samples",
                status="open",
                evidence={
                    "strict_count": summary.get("strict_count"),
                    "with_process_eval": summary.get("with_process_eval"),
                    "gap": process_gap,
                },
                next_action="Backfill only when real match statistics are available; unavailable expected-shot fields must stay null.",
            )
        )
    return items


def _snapshot_items(snapshot_quality: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    strict = max(int(snapshot_quality.get("strict_samples", 0) or 0), 1)
    if snapshot_quality.get("strict_market_snapshot_count", 0) < strict:
        items.append(
            _item(
                item_id="P1-market-snapshot-coverage",
                priority="P1",
                category="market_data",
                title="Improve timestamped market odds coverage for strict samples",
                status="open",
                evidence={
                    "strict_samples": snapshot_quality.get("strict_samples"),
                    "strict_market_snapshot_count": snapshot_quality.get("strict_market_snapshot_count"),
                    "strict_multi_bookmaker_count": snapshot_quality.get("strict_multi_bookmaker_count"),
                    "min_bookmakers": MIN_MARKET_BOOKMAKERS,
                },
                next_action="Capture odds_snapshot/source_timestamps before kickoff; prefer 3+ bookmaker consensus.",
            )
        )
    if snapshot_quality.get("strict_lineup_snapshot_count", 0) < strict:
        items.append(
            _item(
                item_id="P1-lineup-availability-coverage",
                priority="P1",
                category="player_availability",
                title="Add timestamped lineup/player availability evidence",
                status="open",
                evidence={
                    "strict_samples": snapshot_quality.get("strict_samples"),
                    "strict_lineup_snapshot_count": snapshot_quality.get("strict_lineup_snapshot_count"),
                    "strict_injury_snapshot_count": snapshot_quality.get("strict_injury_snapshot_count"),
                },
                next_action="Store available_at/published_at for lineups, injuries, suspensions, and expected minutes; keep shadow-only until gate evidence exists.",
            )
        )
    return items


def _code_items(code_quality: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    pipeline_lines = int(code_quality.get("prediction_pipeline_lines", 0) or 0)
    if pipeline_lines > PIPELINE_LINE_TARGET:
        items.append(
            _item(
                item_id="P2-prediction-pipeline-split",
                priority="P2",
                category="code_quality",
                title="Split prediction_pipeline.py behind stable kernel adapter",
                status="open",
                evidence={"prediction_pipeline_lines": pipeline_lines, "target_lines": PIPELINE_LINE_TARGET},
                next_action="Move I/O, feature assembly, snapshot save, and report generation into focused modules while preserving API/CLI parity tests.",
            )
        )
    return items


def _snapshot_quality(db_path: str | Path, samples: list[dict[str, Any]]) -> dict[str, Any]:
    strict_rows = [row for row in samples if row.get("sample_status") == "strict"]
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if not _has_table(conn, "pre_match_snapshots"):
            return {
                "strict_samples": len(strict_rows),
                "strict_market_snapshot_count": 0,
                "strict_multi_bookmaker_count": 0,
                "strict_lineup_snapshot_count": 0,
                "strict_injury_snapshot_count": 0,
                "min_bookmakers": MIN_MARKET_BOOKMAKERS,
            }
        columns = _table_columns(conn, "pre_match_snapshots")
        market_count = 0
        multi_market_count = 0
        lineup_count = 0
        injury_count = 0
        for row in strict_rows:
            snapshot_id = row.get("pre_match_snapshot_id")
            if not snapshot_id:
                continue
            select_parts = [
                "odds_available" if "odds_available" in columns else "0 AS odds_available",
                "odds_snapshot" if "odds_snapshot" in columns else "NULL AS odds_snapshot",
                "lineup_available" if "lineup_available" in columns else "0 AS lineup_available",
                (
                    "injury_data_available"
                    if "injury_data_available" in columns
                    else "0 AS injury_data_available"
                ),
            ]
            snap = conn.execute(
                f"""
                SELECT {", ".join(select_parts)}
                FROM pre_match_snapshots
                WHERE id=?
                """,
                (snapshot_id,),
            ).fetchone()
            if snap is None:
                continue
            if bool(snap["odds_available"]):
                market_count += 1
                if _bookmaker_count(snap["odds_snapshot"]) >= MIN_MARKET_BOOKMAKERS:
                    multi_market_count += 1
            if bool(snap["lineup_available"]):
                lineup_count += 1
            if bool(snap["injury_data_available"]):
                injury_count += 1
        return {
            "strict_samples": len(strict_rows),
            "strict_market_snapshot_count": market_count,
            "strict_multi_bookmaker_count": multi_market_count,
            "strict_lineup_snapshot_count": lineup_count,
            "strict_injury_snapshot_count": injury_count,
            "min_bookmakers": MIN_MARKET_BOOKMAKERS,
        }
    finally:
        conn.close()


def _bookmaker_count(raw: Any) -> int:
    if not raw:
        return 0
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    for key in ("sample_bookmakers", "web_sample_bookmakers", "bookmaker_count"):
        if payload.get(key) is not None:
            try:
                return int(payload[key])
            except (TypeError, ValueError):
                return 0
    bookmakers = payload.get("bookmakers")
    return len(bookmakers) if isinstance(bookmakers, list) else 0


def _code_quality() -> dict[str, Any]:
    backend_dir = Path(__file__).resolve().parents[2]
    pipeline_path = backend_dir / "app" / "services" / "prediction_pipeline.py"
    lines = sum(1 for _ in pipeline_path.open(encoding="utf-8")) if pipeline_path.exists() else 0
    return {"prediction_pipeline_lines": lines, "prediction_pipeline_target_lines": PIPELINE_LINE_TARGET}


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _item(
    *,
    item_id: str,
    priority: str,
    category: str,
    title: str,
    status: str,
    evidence: dict[str, Any],
    next_action: str,
) -> dict[str, Any]:
    return {
        "id": item_id,
        "priority": priority,
        "category": category,
        "title": title,
        "status": status,
        "evidence": evidence,
        "next_action": next_action,
    }
