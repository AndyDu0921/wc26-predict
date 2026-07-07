"""Build a read-only strict-sample repair queue.

This module inspects local evidence candidates for diagnostic/rejected
evaluation samples.  It never creates snapshots, probabilities, weights,
artifacts, or reports.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.evaluation_registry import WC26_COMPETITION, build_evaluation_registry
from app.services.evaluation_registry_repair import build_evaluation_registry_repair_report


def build_strict_sample_repair_queue(
    db_path: str | Path,
    *,
    competition: str = WC26_COMPETITION,
    include_strict: bool = False,
) -> dict[str, Any]:
    """Return a deterministic queue for strict-sample data repair."""
    registry = build_evaluation_registry(db_path, competition=competition)
    repair_report = build_evaluation_registry_repair_report(
        db_path,
        competition=competition,
        include_strict=include_strict,
    )
    repair_by_sample = {row["sample_id"]: row for row in repair_report["samples"]}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = []
        for sample in registry["samples"]:
            if not include_strict and sample["sample_status"] == "strict":
                continue
            repair_row = repair_by_sample.get(sample["sample_id"]) or {}
            evidence = _local_evidence(conn, sample)
            rows.append(_queue_row(sample, repair_row, evidence))
    finally:
        conn.close()

    rows = sorted(rows, key=lambda row: (row["repair_order"], row["sample_id"]))
    repair_class_counts = Counter(row["repair_class"] for row in rows)
    local_status_counts = Counter(row["local_evidence_status"] for row in rows)
    return {
        "schema_version": "strict_sample_repair_queue.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "competition": competition,
        "registry_hash": registry["registry_hash"],
        "summary": {
            "queued_samples": len(rows),
            "strict_count": registry["summary"].get("strict_count"),
            "diagnostic_count": registry["summary"].get("diagnostic_count"),
            "rejected_count": registry["summary"].get("rejected_count"),
            "local_repair_ready_count": sum(
                1 for row in rows if row["can_repair_from_local_evidence"]
            ),
            "needs_external_evidence_count": sum(
                1 for row in rows if row["repair_class"] == "needs_external_pre_match_snapshot"
            ),
            "hard_blocked_count": sum(
                1 for row in rows if row["repair_class"].startswith("hard_block")
            ),
            "repair_class_counts": dict(repair_class_counts),
            "local_evidence_status_counts": dict(local_status_counts),
        },
        "samples": rows,
        "notes": (
            "Read-only repair queue. Local candidates are informational only; "
            "strict promotion still requires real pre-kickoff timestamped probabilities."
        ),
    }


def _queue_row(
    sample: dict[str, Any],
    repair_row: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    hard_blocked = repair_row.get("blocking_level") == "hard_block"
    has_usable = bool(evidence["usable_candidates"])
    if sample["sample_status"] == "strict":
        repair_class = "already_strict"
        next_action = "No repair needed."
    elif has_usable and not hard_blocked:
        repair_class = "ready_for_manual_import_from_local_evidence"
        next_action = (
            "Review usable local candidate(s), then import/link only the real "
            "pre-kickoff probability evidence with audit notes."
        )
    elif hard_blocked:
        repair_class = "hard_block_requires_replacement_or_result_reconcile"
        next_action = (
            "Do not promote until hard-blocking evidence is replaced or result "
            "sources are reconciled."
        )
    else:
        repair_class = "needs_external_pre_match_snapshot"
        next_action = (
            "Find real pre-kickoff snapshot/probability evidence externally or "
            "leave the sample diagnostic."
        )

    return {
        "sample_id": sample["sample_id"],
        "sample_status": sample["sample_status"],
        "repair_class": repair_class,
        "repair_order": int(repair_row.get("repair_order", 90) or 90),
        "priority": repair_row.get("priority", "P1"),
        "blocking_level": repair_row.get("blocking_level", "manual_review"),
        "home_team": sample["home_team"],
        "away_team": sample["away_team"],
        "match_date": sample.get("match_date"),
        "kickoff_at": sample.get("kickoff_at"),
        "as_of_time": sample.get("as_of_time"),
        "stage": sample.get("stage"),
        "canonical_match_id": sample.get("canonical_match_id"),
        "canonical_result_source": sample.get("canonical_result_source"),
        "current_prob_source": sample.get("current_prob_source"),
        "exclusion_reasons": sample.get("exclusion_reasons") or [],
        "recommended_actions": repair_row.get("recommended_actions") or [],
        "local_evidence_status": evidence["status"],
        "can_repair_from_local_evidence": has_usable and not hard_blocked,
        "local_evidence": evidence,
        "next_action": next_action,
    }


def _local_evidence(conn: sqlite3.Connection, sample: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    candidates.extend(_pre_match_candidates(conn, sample))
    candidates.extend(_prediction_candidates(conn, sample))
    usable = [item for item in candidates if item["usable_for_strict_repair"]]
    blocked = [item for item in candidates if not item["usable_for_strict_repair"]]
    if usable:
        status = "usable_local_pre_kickoff_probability_found"
    elif candidates:
        status = "local_candidates_found_but_not_strict_usable"
    else:
        status = "no_local_snapshot_candidates"
    return {
        "status": status,
        "candidate_count": len(candidates),
        "usable_candidate_count": len(usable),
        "blocked_candidate_count": len(blocked),
        "usable_candidates": usable,
        "blocked_candidates": blocked[:10],
    }


def _pre_match_candidates(conn: sqlite3.Connection, sample: dict[str, Any]) -> list[dict[str, Any]]:
    if not _has_table(conn, "pre_match_snapshots"):
        return []
    columns = _table_columns(conn, "pre_match_snapshots")
    select_columns = [
        "id",
        "match_id",
        "home_team",
        "away_team",
        "snapshot_at",
        "kickoff_at" if "kickoff_at" in columns else "NULL AS kickoff_at",
        "final_home_prob" if "final_home_prob" in columns else "NULL AS final_home_prob",
        "final_draw_prob" if "final_draw_prob" in columns else "NULL AS final_draw_prob",
        "final_away_prob" if "final_away_prob" in columns else "NULL AS final_away_prob",
        "component_probs" if "component_probs" in columns else "NULL AS component_probs",
        "weight_config_label" if "weight_config_label" in columns else "NULL AS weight_config_label",
        "model_version" if "model_version" in columns else "NULL AS model_version",
    ]
    rows = _matching_rows(
        conn,
        table_name="pre_match_snapshots",
        select_columns=select_columns,
        sample=sample,
        timestamp_column="snapshot_at",
    )
    return [_candidate_from_pre_snapshot(row, sample) for row in rows]


def _prediction_candidates(conn: sqlite3.Connection, sample: dict[str, Any]) -> list[dict[str, Any]]:
    if not _has_table(conn, "prediction_snapshots"):
        return []
    columns = _table_columns(conn, "prediction_snapshots")
    select_columns = [
        "id",
        "match_id",
        "home_team",
        "away_team",
        "generated_at",
        "model_version" if "model_version" in columns else "NULL AS model_version",
        "adjusted_probs" if "adjusted_probs" in columns else "NULL AS adjusted_probs",
        "baseline_probs" if "baseline_probs" in columns else "NULL AS baseline_probs",
        "component_probs" if "component_probs" in columns else "NULL AS component_probs",
    ]
    rows = _matching_rows(
        conn,
        table_name="prediction_snapshots",
        select_columns=select_columns,
        sample=sample,
        timestamp_column="generated_at",
    )
    return [_candidate_from_prediction_snapshot(row, sample) for row in rows]


def _matching_rows(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    select_columns: list[str],
    sample: dict[str, Any],
    timestamp_column: str,
) -> list[sqlite3.Row]:
    ids = {
        str(item)
        for item in (
            sample.get("match_result_id"),
            sample.get("schedule_id"),
            sample.get("schedule_match_number"),
            sample.get("canonical_match_id"),
        )
        if item is not None
    }
    rows: list[sqlite3.Row] = []
    seen: set[str] = set()
    select_sql = ", ".join(select_columns)
    if ids:
        placeholders = ",".join("?" for _ in ids)
        for row in conn.execute(
            f"""
            SELECT {select_sql}
            FROM {table_name}
            WHERE CAST(match_id AS TEXT) IN ({placeholders})
            ORDER BY {timestamp_column} DESC
            """,
            sorted(ids),
        ).fetchall():
            _append_matching_row(rows, seen, row, sample)
    for row in conn.execute(
        f"""
        SELECT {select_sql}
        FROM {table_name}
        WHERE home_team=? AND away_team=?
        ORDER BY {timestamp_column} DESC
        """,
        (sample["home_team"], sample["away_team"]),
    ).fetchall():
        _append_matching_row(rows, seen, row, sample)
    return rows


def _append_matching_row(
    rows: list[sqlite3.Row],
    seen: set[str],
    row: sqlite3.Row,
    sample: dict[str, Any],
) -> None:
    row_id = str(row["id"])
    if row_id in seen:
        return
    if _norm(row["home_team"]) != _norm(sample["home_team"]):
        return
    if _norm(row["away_team"]) != _norm(sample["away_team"]):
        return
    rows.append(row)
    seen.add(row_id)


def _candidate_from_pre_snapshot(row: sqlite3.Row, sample: dict[str, Any]) -> dict[str, Any]:
    probs = _pre_snapshot_probs(row)
    reasons = _candidate_blockers(
        as_of_time=row["snapshot_at"],
        kickoff_at=sample.get("kickoff_at"),
        probs=probs,
    )
    return {
        "source_table": "pre_match_snapshots",
        "id": row["id"],
        "match_id": row["match_id"],
        "as_of_time": row["snapshot_at"],
        "candidate_kickoff_at": row["kickoff_at"],
        "model_version": row["model_version"],
        "weight_config_label": row["weight_config_label"],
        "prob_source": "final_home_draw_away_prob",
        "current_probs": probs,
        "component_count": _component_count(row["component_probs"]),
        "usable_for_strict_repair": not reasons,
        "block_reasons": reasons,
    }


def _candidate_from_prediction_snapshot(row: sqlite3.Row, sample: dict[str, Any]) -> dict[str, Any]:
    probs = _prediction_snapshot_probs(row)
    reasons = _candidate_blockers(
        as_of_time=row["generated_at"],
        kickoff_at=sample.get("kickoff_at"),
        probs=probs,
    )
    return {
        "source_table": "prediction_snapshots",
        "id": row["id"],
        "match_id": row["match_id"],
        "as_of_time": row["generated_at"],
        "candidate_kickoff_at": None,
        "model_version": row["model_version"],
        "weight_config_label": "prediction_snapshot.adjusted_or_baseline_probs",
        "prob_source": "adjusted_or_baseline_probs",
        "current_probs": probs,
        "component_count": _component_count(row["component_probs"]),
        "usable_for_strict_repair": not reasons,
        "block_reasons": reasons,
    }


def _candidate_blockers(
    *,
    as_of_time: Any,
    kickoff_at: str | None,
    probs: dict[str, float] | None,
) -> list[str]:
    reasons = []
    snap = _parse_dt(_as_optional_str(as_of_time))
    kickoff = _parse_dt(kickoff_at)
    if snap is None:
        reasons.append("candidate_as_of_time_unknown")
    if kickoff is None:
        reasons.append("sample_kickoff_time_unknown")
    if snap is not None and kickoff is not None and snap > kickoff:
        reasons.append("candidate_after_kickoff")
    if probs is None:
        reasons.append("missing_current_probabilities")
    return reasons


def _pre_snapshot_probs(row: sqlite3.Row) -> dict[str, float] | None:
    return _normalize_probs(
        row["final_home_prob"],
        row["final_draw_prob"],
        row["final_away_prob"],
    )


def _prediction_snapshot_probs(row: sqlite3.Row) -> dict[str, float] | None:
    for column in ("adjusted_probs", "baseline_probs"):
        probs = _probs_from_mapping(_json_loads(row[column]))
        if probs is not None:
            return probs
    return None


def _probs_from_mapping(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    return _normalize_probs(
        raw.get("home", raw.get("home_win_prob", raw.get("home_prob"))),
        raw.get("draw", raw.get("draw_prob")),
        raw.get("away", raw.get("away_win_prob", raw.get("away_prob"))),
    )


def _normalize_probs(home_raw: Any, draw_raw: Any, away_raw: Any) -> dict[str, float] | None:
    try:
        home = float(home_raw)
        draw = float(draw_raw)
        away = float(away_raw)
    except (TypeError, ValueError):
        return None
    if min(home, draw, away) < 0:
        return None
    total = home + draw + away
    if total <= 0:
        return None
    return {"home": home / total, "draw": draw / total, "away": away / total}


def _component_count(raw: Any) -> int:
    parsed = _json_loads(raw)
    if not isinstance(parsed, dict):
        return 0
    return sum(1 for value in parsed.values() if value)


def _json_loads(raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    if len(text) == 10:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
