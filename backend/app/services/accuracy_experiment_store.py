"""Persistence helpers for V4.8 shadow experiment audit tables."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4


def persist_experiment_result(db_path: str | Path, result: dict[str, Any]) -> dict[str, Any]:
    """Persist one shadow experiment result and optional per-sample predictions.

    This writes only audit tables.  It never mutates production weights,
    model artifacts, or prediction snapshots.
    """
    path = Path(db_path)
    conn = sqlite3.connect(str(path))
    try:
        _require_tables(conn)
        experiment_id = str(result["experiment_id"])
        existing = conn.execute(
            "SELECT id FROM experiment_runs WHERE experiment_id=?",
            (experiment_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO experiment_runs (
                    id, experiment_id, candidate_name, champion_name,
                    sample_registry_hash, status, n_samples,
                    metrics_current, metrics_candidate, paired_deltas,
                    group_metrics, leakage_checks, gate_decision, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    experiment_id,
                    result.get("candidate_name", ""),
                    result.get("champion_name", "current_fusion"),
                    result.get("sample_registry_hash", ""),
                    result.get("status", "unknown"),
                    int(result.get("n_samples", 0) or 0),
                    _json(result.get("metrics_current")),
                    _json(result.get("metrics_candidate")),
                    _json(result.get("paired_deltas")),
                    _json(result.get("group_metrics")),
                    _json(result.get("leakage_checks")),
                    _json(result.get("gate_decision")),
                    result.get("notes"),
                ),
            )
        inserted_predictions = 0
        for pred in result.get("candidate_predictions") or []:
            exists = conn.execute(
                """
                SELECT 1 FROM candidate_predictions
                WHERE experiment_id=? AND sample_id=? AND candidate_name=?
                LIMIT 1
                """,
                (experiment_id, pred.get("sample_id"), result.get("candidate_name", "")),
            ).fetchone()
            if exists is not None:
                continue
            conn.execute(
                """
                INSERT INTO candidate_predictions (
                    id, experiment_id, sample_id, candidate_name,
                    actual_result, current_probs, candidate_probs, component_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    experiment_id,
                    pred.get("sample_id", ""),
                    result.get("candidate_name", ""),
                    pred.get("actual_result"),
                    _json(pred.get("current_probs")),
                    _json(pred.get("candidate_probs")),
                    _json(pred.get("component_payload")),
                ),
            )
            inserted_predictions += 1
        conn.commit()
        return {
            "experiment_id": experiment_id,
            "experiment_inserted": existing is None,
            "candidate_predictions_inserted": inserted_predictions,
        }
    finally:
        conn.close()


def _require_tables(conn: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing = {"experiment_runs", "candidate_predictions"} - tables
    if missing:
        raise RuntimeError(f"Missing accuracy-engine tables: {sorted(missing)}")


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)
