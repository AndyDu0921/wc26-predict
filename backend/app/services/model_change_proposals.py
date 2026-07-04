"""Generic V4.8 model-change proposal writer.

The proposal ledger is the self-evolution boundary: the system can suggest
changes, but this module never applies them to production configuration.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class ModelChangeProposalCandidate:
    proposal_type: str
    candidate_name: str
    source: str
    status: str
    sample_registry_hash: str | None
    candidate_payload: dict[str, Any]
    gate_decision: dict[str, Any]
    base_payload: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    target_table: str | None = None
    target_key: str | None = None
    notes: str | None = None

    def fingerprint(self) -> str:
        payload = asdict(self)
        payload.pop("notes", None)
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fingerprint"] = self.fingerprint()
        return payload


def build_proposal_from_experiment(result: dict[str, Any]) -> ModelChangeProposalCandidate:
    gate = result.get("gate_decision") or {}
    passed = bool(gate.get("passed"))
    status = "proposal_pending_review" if passed else "proposal_rejected"
    candidate_name = str(result.get("candidate_name", "unknown_candidate"))
    metrics = {
        "metrics_current": result.get("metrics_current"),
        "metrics_candidate": result.get("metrics_candidate"),
        "paired_deltas": result.get("paired_deltas"),
        "group_metrics": result.get("group_metrics"),
    }
    return ModelChangeProposalCandidate(
        proposal_type="model",
        candidate_name=candidate_name,
        source="shadow_experiment",
        status=status,
        sample_registry_hash=result.get("sample_registry_hash"),
        base_payload={"champion_name": result.get("champion_name", "current_fusion")},
        candidate_payload={
            "candidate_name": candidate_name,
            "experiment_id": result.get("experiment_id"),
            "shadow_only": True,
        },
        metrics=metrics,
        gate_decision=gate,
        evidence={
            "experiment_status": result.get("status"),
            "n_samples": result.get("n_samples"),
            "leakage_checks": result.get("leakage_checks"),
            "candidate_availability": result.get("candidate_availability"),
            "unavailable_reasons": result.get("unavailable_reasons"),
        },
        notes="Generated from shadow experiment; does not apply production changes.",
    )


def build_learning_log_weight_proposal(
    db_path: str | Path,
    *,
    sample_registry_hash: str | None = None,
    min_active_logs: int = 30,
) -> ModelChangeProposalCandidate:
    """Build a proposal from learning-log marginal attribution evidence."""
    path = Path(db_path)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        if not _has_table(conn, "prediction_learning_log"):
            rows = []
        else:
            rows = conn.execute(
                """
                SELECT
                    status, learning_weight, learning_tier,
                    dc_marginal, enhancer_marginal, elo_marginal,
                    market_marginal, signal_marginal,
                    score_log_loss, score_exact_hit, score_top3_hit
                FROM prediction_learning_log
                WHERE status='active'
                   OR learning_tier IN ('full', 'diagnostic')
                """
            ).fetchall()
    finally:
        conn.close()

    active_rows = [dict(row) for row in rows]
    enough_logs = len(active_rows) >= min_active_logs
    gate = {
        "passed": False,
        "status": "proposal_needs_backtest" if enough_logs else "proposal_rejected",
        "reasons": (
            ["requires_paired_walk_forward_experiment"]
            if enough_logs
            else [f"active_learning_logs {len(active_rows)} < {min_active_logs}"]
        ),
    }
    marginals = _mean_marginals(active_rows)
    recommendations = _marginal_recommendations(marginals)
    return ModelChangeProposalCandidate(
        proposal_type="weights",
        candidate_name="learning_log_marginal_review",
        source="self_evolution_learning_log",
        status="proposal_needs_backtest" if enough_logs else "proposal_rejected",
        sample_registry_hash=sample_registry_hash,
        base_payload=None,
        candidate_payload={
            "recommendations": recommendations,
            "production_mutation": False,
        },
        metrics={
            "active_learning_logs": len(active_rows),
            "mean_marginals": marginals,
        },
        gate_decision=gate,
        evidence={
            "min_active_logs": min_active_logs,
            "score_metric_rows": sum(1 for row in active_rows if row.get("score_log_loss") is not None),
        },
        notes="Proposal-only marginal review; no production weights were changed.",
    )


def persist_model_change_proposal(
    db_path: str | Path,
    proposal: ModelChangeProposalCandidate,
) -> dict[str, Any]:
    """Persist a generic model-change proposal idempotently by fingerprint."""
    path = Path(db_path)
    conn = sqlite3.connect(str(path))
    try:
        _require_table(conn)
        fingerprint = proposal.fingerprint()
        existing = _find_existing_by_fingerprint(conn, fingerprint)
        if existing is not None:
            return {"inserted": False, "id": existing[0], "fingerprint": fingerprint}
        evidence = dict(proposal.evidence or {})
        evidence["fingerprint"] = fingerprint
        record_id = str(uuid4())
        conn.execute(
            """
            INSERT INTO model_change_proposals (
                id, proposal_type, candidate_name, source, status,
                sample_registry_hash, target_table, target_key,
                base_payload, candidate_payload, metrics, gate_decision,
                evidence, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                proposal.proposal_type,
                proposal.candidate_name,
                proposal.source,
                proposal.status,
                proposal.sample_registry_hash,
                proposal.target_table,
                proposal.target_key,
                _json(proposal.base_payload),
                _json(proposal.candidate_payload),
                _json(proposal.metrics),
                _json(proposal.gate_decision),
                _json(evidence),
                proposal.notes,
            ),
        )
        conn.commit()
        return {"inserted": True, "id": record_id, "fingerprint": fingerprint}
    finally:
        conn.close()


def _mean_marginals(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    result = {}
    for key in ("dc_marginal", "enhancer_marginal", "elo_marginal", "market_marginal", "signal_marginal"):
        values = [float(row[key]) for row in rows if row.get(key) is not None]
        result[key] = round(sum(values) / len(values), 6) if values else None
    return result


def _marginal_recommendations(marginals: dict[str, float | None]) -> list[dict[str, Any]]:
    recommendations = []
    for key, value in marginals.items():
        if value is None:
            continue
        component = key.replace("_marginal", "")
        if value > 0.002:
            action = "consider_increase"
        elif value < -0.002:
            action = "consider_decrease"
        else:
            action = "hold"
        recommendations.append({"component": component, "mean_marginal": value, "action": action})
    return recommendations


def _require_table(conn: sqlite3.Connection) -> None:
    if not _has_table(conn, "model_change_proposals"):
        raise RuntimeError("Missing model_change_proposals table; run Alembic upgrade first")


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _find_existing_by_fingerprint(conn: sqlite3.Connection, fingerprint: str) -> tuple[Any, ...] | None:
    for row in conn.execute("SELECT id, evidence FROM model_change_proposals"):
        try:
            evidence = json.loads(row[1] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if evidence.get("fingerprint") == fingerprint:
            return row
    return None


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)
