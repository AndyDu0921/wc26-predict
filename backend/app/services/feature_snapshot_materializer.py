"""Materialize registry feature snapshots into V4.9 audit tables.

Feature snapshots are pre-result payloads used by experiments.  They do not
contain actual goals or outcome labels, and persisting them never changes
production weights, model artifacts, reports, or prediction snapshots.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.services.evaluation_registry import build_evaluation_registry
from app.services.information_state_signals import build_information_state_signals
from app.services.information_state_engine import build_match_information_state_snapshot
from app.services.player_availability import build_player_availability_shadow


def build_feature_snapshot_records(
    db_path: str | Path,
    *,
    sample_status: str = "strict",
    competition: str = "FIFA World Cup 2026",
) -> list[dict[str, Any]]:
    """Build deterministic feature snapshot records from registry samples."""
    registry = build_evaluation_registry(db_path, competition=competition)
    records = []
    for row in registry["samples"]:
        if sample_status != "all" and row["sample_status"] != sample_status:
            continue
        if row["current_probs"] is None:
            continue
        payload = _feature_payload(row, registry["registry_hash"], db_path=db_path)
        records.append(
            {
                "sample_id": row["sample_id"],
                "match_id": row["canonical_match_id"],
                "source": "evaluation_registry.v2",
                "as_of_time": row["as_of_time"],
                "kickoff_at": row["kickoff_at"],
                "horizon_hours": row["horizon_hours"],
                "feature_hash": _stable_hash(payload),
                "payload": payload,
                "data_availability": row["data_availability"],
                "leakage_status": row["leakage_status"],
            }
        )
    return records


def persist_feature_snapshot_records(
    db_path: str | Path,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist feature snapshot audit records idempotently."""
    path = Path(db_path)
    conn = sqlite3.connect(str(path))
    try:
        _require_table(conn)
        inserted = 0
        skipped = 0
        for record in records:
            existing = conn.execute(
                """
                SELECT 1 FROM feature_snapshots
                WHERE sample_id=? AND feature_hash=?
                LIMIT 1
                """,
                (record["sample_id"], record["feature_hash"]),
            ).fetchone()
            if existing is not None:
                skipped += 1
                continue
            conn.execute(
                """
                INSERT INTO feature_snapshots (
                    id, sample_id, match_id, source, as_of_time, kickoff_at,
                    horizon_hours, feature_hash, payload, data_availability,
                    leakage_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    record["sample_id"],
                    record["match_id"],
                    record["source"],
                    record["as_of_time"],
                    record["kickoff_at"],
                    record["horizon_hours"],
                    record["feature_hash"],
                    _json(record["payload"]),
                    _json(record["data_availability"]),
                    record["leakage_status"],
                ),
            )
            inserted += 1
        conn.commit()
        return {"inserted": inserted, "skipped": skipped, "total": len(records)}
    finally:
        conn.close()


def _feature_payload(row: dict[str, Any], registry_hash: str, *, db_path: str | Path) -> dict[str, Any]:
    quality = _feature_quality(row)
    information_state = build_information_state_signals(
        row["home_team"],
        row["away_team"],
        as_of_time=row.get("as_of_time"),
        kickoff_at=row.get("kickoff_at"),
    )
    player_availability = build_player_availability_shadow(
        row["home_team"],
        row["away_team"],
        db_path=db_path,
        as_of_time=row.get("as_of_time"),
    ).to_dict()
    information_state_v4_10 = build_match_information_state_snapshot(
        db_path,
        match_id=row.get("canonical_match_id"),
        home_team=row["home_team"],
        away_team=row["away_team"],
        kickoff_at=row.get("kickoff_at"),
    )
    return {
        "schema_version": "feature_snapshot.v2",
        "registry_hash": registry_hash,
        "sample_id": row["sample_id"],
        "canonical_match_id": row["canonical_match_id"],
        "canonical_result_source": row["canonical_result_source"],
        "home_team": row["home_team"],
        "away_team": row["away_team"],
        "stage": row["stage"],
        "kickoff_at": row["kickoff_at"],
        "kickoff_source": row["kickoff_source"],
        "as_of_time": row["as_of_time"],
        "horizon_hours": row["horizon_hours"],
        "horizon_bucket": row["horizon_bucket"],
        "model_version": row["model_version"],
        "weight_config_label": row["weight_config_label"],
        "pre_match_snapshot_id": row["pre_match_snapshot_id"],
        "prediction_snapshot_id": row["prediction_snapshot_id"],
        "component_count": row["component_count"],
        "data_completeness_score": row["data_completeness_score"],
        "data_availability": row["data_availability"],
        "feature_quality_score": quality["score"],
        "quality_flags": quality["flags"],
        "current_probs": row["current_probs"],
        "current_prob_source": row.get("current_prob_source"),
        "score_matrix_available": bool(row["data_availability"].get("score_matrix")),
        "leakage_status": row["leakage_status"],
        "information_state_signals": information_state["signals"],
        "information_state_signal_summary": information_state["summary"],
        "information_state_v4_10": information_state_v4_10,
        "player_availability_shadow": player_availability,
        "schedule_context": _schedule_context(row),
    }


def _feature_quality(row: dict[str, Any]) -> dict[str, Any]:
    flags = []
    score = 0.0
    if row.get("leakage_status") == "clean":
        score += 0.30
    else:
        flags.append(f"leakage_status:{row.get('leakage_status')}")
    if row.get("current_probs"):
        score += 0.20
    else:
        flags.append("missing_current_probs")
    if row.get("horizon_hours") is not None:
        score += 0.15
    else:
        flags.append("missing_horizon")
    if row.get("component_count", 0) >= 2:
        score += 0.10
    else:
        flags.append("low_component_count")
    availability = row.get("data_availability") or {}
    if availability.get("score_matrix"):
        score += 0.10
    else:
        flags.append("missing_score_matrix")
    if availability.get("process_eval"):
        score += 0.05
    else:
        flags.append("missing_process_eval")
    completeness = float(row.get("data_completeness_score") or 0.0)
    score += 0.10 * max(0.0, min(1.0, completeness))
    return {"score": round(score, 4), "flags": flags}


def _schedule_context(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": row.get("stage"),
        "horizon_hours": row.get("horizon_hours"),
        "horizon_bucket": row.get("horizon_bucket"),
        "rest_days": None,
        "travel_distance_km": None,
        "source_status": {
            "rest_days": "unavailable",
            "travel_distance_km": "unavailable",
            "reason": "not_materialized_in_registry_v2",
            "shadow_only": True,
        },
    }


def _require_table(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feature_snapshots'",
    ).fetchone()
    if row is None:
        raise RuntimeError("Missing feature_snapshots table; run Alembic upgrade first")


def _stable_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)
