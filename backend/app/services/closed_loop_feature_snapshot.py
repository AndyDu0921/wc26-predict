"""Closed-loop feature snapshot persistence from pre-match snapshots.

This module bridges the production prediction path and the accuracy engine. It
does not change probabilities or model weights; it only materializes the
pre-result information state that future backtests and audits need.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.services.evaluation_registry import DEFAULT_DB_PATH
from app.services.information_state_engine import build_match_information_state_snapshot


def persist_feature_snapshot_from_latest_prematch(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    match_id: str,
) -> dict[str, Any]:
    """Persist one feature_snapshots row from the latest pre_match_snapshots row.

    The write is idempotent by ``sample_id`` + ``feature_hash``. If the feature
    snapshot already exists, the function returns ``inserted=False``.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if not _has_table(conn, "feature_snapshots"):
            return {"inserted": False, "reason": "missing_feature_snapshots_table"}
        row = conn.execute(
            """
            SELECT *
            FROM pre_match_snapshots
            WHERE CAST(match_id AS TEXT) = ?
            ORDER BY snapshot_at DESC, id DESC
            LIMIT 1
            """,
            (str(match_id),),
        ).fetchone()
        if row is None:
            return {"inserted": False, "reason": "missing_pre_match_snapshot"}

        payload = _payload_from_snapshot(dict(row), db_path=db_path)
        feature_hash = _stable_hash(payload)
        sample_id = f"prematch:{row['match_id']}:{row['id']}"
        existing = conn.execute(
            """
            SELECT id
            FROM feature_snapshots
            WHERE sample_id = ? AND feature_hash = ?
            LIMIT 1
            """,
            (sample_id, feature_hash),
        ).fetchone()
        if existing is not None:
            return {
                "inserted": False,
                "reason": "already_exists",
                "feature_snapshot_id": existing["id"],
                "sample_id": sample_id,
                "feature_hash": feature_hash,
            }

        data_availability = _data_availability(row)
        leakage_status = "clean" if not _is_after(row["snapshot_at"], row["kickoff_at"]) else "leaky_after_kickoff"
        horizon_hours = _horizon_hours(row["snapshot_at"], row["kickoff_at"])
        feature_id = str(uuid4())
        conn.execute(
            """
            INSERT INTO feature_snapshots (
                id, sample_id, match_id, source, as_of_time, kickoff_at,
                horizon_hours, feature_hash, payload, data_availability,
                leakage_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feature_id,
                sample_id,
                str(row["match_id"]),
                "pre_match_snapshots.closed_loop",
                row["snapshot_at"],
                row["kickoff_at"],
                horizon_hours,
                feature_hash,
                _json(payload),
                _json(data_availability),
                leakage_status,
            ),
        )
        conn.commit()
        return {
            "inserted": True,
            "feature_snapshot_id": feature_id,
            "sample_id": sample_id,
            "feature_hash": feature_hash,
            "leakage_status": leakage_status,
        }
    finally:
        conn.close()


def _payload_from_snapshot(row: dict[str, Any], *, db_path: str | Path) -> dict[str, Any]:
    information_state = build_match_information_state_snapshot(
        db_path,
        match_id=str(row["match_id"]),
        home_team=row.get("home_team"),
        away_team=row.get("away_team"),
        kickoff_at=row.get("kickoff_at"),
        as_of_time=row.get("snapshot_at"),
    )
    return {
        "schema_version": "feature_snapshot.v2.closed_loop",
        "source": "pre_match_snapshots",
        "pre_match_snapshot_id": row.get("id"),
        "match_id": str(row.get("match_id")),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        "competition": row.get("competition"),
        "as_of_time": row.get("snapshot_at"),
        "kickoff_at": row.get("kickoff_at"),
        "model_version": row.get("model_version") or row.get("code_version"),
        "prediction_mode": row.get("prediction_mode"),
        "probabilities": {
            "home": row.get("final_home_prob"),
            "draw": row.get("final_draw_prob"),
            "away": row.get("final_away_prob"),
        },
        "expected_goals": {
            "home": row.get("home_xg"),
            "away": row.get("away_xg"),
        },
        "component_probs": _loads(row.get("component_probs"), {}),
        "market_odds": _loads(row.get("odds_snapshot"), None),
        "weather": _loads(row.get("weather_snapshot"), None),
        "top_scores": _loads(row.get("top_scores"), []),
        "fused_score_matrix_available": bool(_loads(row.get("fused_score_matrix"), None)),
        "source_score_matrices_available": bool(_loads(row.get("source_score_matrices"), None)),
        "risk_tags": _loads(row.get("risk_tags"), []),
        "missing_inputs": _loads(row.get("missing_inputs"), []),
        "data_availability": _data_availability(row),
        "information_state_v4_10": information_state,
    }


def _data_availability(row: sqlite3.Row | dict[str, Any]) -> dict[str, bool]:
    return {
        "weather": bool(row["weather_available"]),
        "market_odds": bool(row["odds_available"]),
        "lineup": bool(row["lineup_available"]),
        "injury": bool(row["injury_data_available"]),
        "news_signals": bool(row["news_signals_available"]),
        "score_matrix": bool(_loads(row["fused_score_matrix"], None)),
    }


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _loads(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _json(payload: Any) -> str:
    return json.dumps(payload if payload is not None else {}, ensure_ascii=False, sort_keys=True)


def _stable_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_after(left: Any, right: Any) -> bool:
    left_dt = _parse_dt(left)
    right_dt = _parse_dt(right)
    if left_dt is None or right_dt is None:
        return False
    return left_dt > right_dt


def _horizon_hours(snapshot_at: Any, kickoff_at: Any) -> float | None:
    snapshot_dt = _parse_dt(snapshot_at)
    kickoff_dt = _parse_dt(kickoff_at)
    if snapshot_dt is None or kickoff_dt is None:
        return None
    return round((kickoff_dt - snapshot_dt).total_seconds() / 3600.0, 4)
