"""Generic V4.9 model-change proposal writer.

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


ALLOWED_PROPOSAL_STATUSES = {
    "proposal_rejected",
    "proposal_pending_data_repair",
    "proposal_needs_backtest",
    "proposal_pending_review",
    "approved_for_shadow",
    "promoted_config",
}
MANUAL_APPROVAL_STATUSES = {"approved_for_shadow", "promoted_config"}


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
        payload = _fingerprint_payload(asdict(self))
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
        proposal_type=_proposal_type_for_candidate(candidate_name),
        candidate_name=candidate_name,
        source="shadow_experiment",
        status=status,
        sample_registry_hash=result.get("sample_registry_hash"),
        base_payload={"champion_name": result.get("champion_name", "current_fusion")},
        candidate_payload={
            "candidate_name": candidate_name,
            "candidate_family": result.get("candidate_family"),
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


def build_data_repair_proposal_from_repair_report(report: dict[str, Any]) -> ModelChangeProposalCandidate:
    """Build a proposal-only data repair plan from the registry repair report."""
    repair_summary = dict(report.get("repair_summary") or {})
    action_counts = dict(repair_summary.get("action_counts") or {})
    needs_repair = bool(action_counts) or int(repair_summary.get("reported_samples", 0) or 0) > 0
    gate = {
        "passed": False,
        "status": "proposal_data_repair_only" if needs_repair else "proposal_rejected",
        "reasons": (
            ["requires_real_pre_kickoff_evidence_before_sample_promotion"]
            if needs_repair
            else ["no_registry_repair_action_needed"]
        ),
    }
    return ModelChangeProposalCandidate(
        proposal_type="data-repair",
        candidate_name="evaluation_registry_repair_plan",
        source="evaluation_registry_repair_report",
        status="proposal_pending_data_repair" if needs_repair else "proposal_rejected",
        sample_registry_hash=report.get("registry_hash"),
        base_payload={"summary": report.get("summary")},
        candidate_payload={
            "repair_summary": repair_summary,
            "action_counts": action_counts,
            "production_mutation": False,
            "artifact_mutation": False,
            "historical_report_mutation": False,
        },
        metrics={
            "reported_samples": repair_summary.get("reported_samples"),
            "potentially_promotable_count": repair_summary.get("potentially_promotable_count"),
            "must_remain_rejected_count": repair_summary.get("must_remain_rejected_count"),
        },
        gate_decision=gate,
        evidence={
            "schema_version": report.get("schema_version"),
            "sample_status_counts": repair_summary.get("sample_status_counts"),
            "notes": report.get("notes"),
        },
        notes="Proposal-only data repair plan; it does not create snapshots, probabilities, artifacts, or reports.",
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


def build_registry_feature_rule_proposal(registry: dict[str, Any]) -> ModelChangeProposalCandidate:
    """Build a data/feature-rule proposal from evaluation registry diagnostics."""
    summary = dict(registry.get("summary") or {})
    samples = registry.get("samples") or []
    leakage_counts = _count_values(samples, "leakage_status")
    exclusion_counts = _exclusion_reason_counts(samples)
    strict_count = int(summary.get("strict_count", summary.get("eligible_backtest_count", 0)) or 0)
    diagnostic_count = int(summary.get("diagnostic_count", 0) or 0)
    rejected_count = int(summary.get("rejected_count", 0) or 0)
    needs_repair = (
        strict_count < 30
        or diagnostic_count > 0
        or rejected_count > 0
        or int(summary.get("source_result_conflicts", 0) or 0) > 0
    )
    actions = _registry_repair_actions(summary, leakage_counts, exclusion_counts)
    gate = {
        "passed": False,
        "status": "proposal_data_quality_only" if needs_repair else "proposal_rejected",
        "reasons": (
            ["requires_data_repair_before_model_change"]
            if needs_repair
            else ["no_registry_quality_action_needed"]
        ),
    }
    return ModelChangeProposalCandidate(
        proposal_type="feature-rule",
        candidate_name="evaluation_registry_quality_repair",
        source="self_evolution_registry",
        status="proposal_pending_data_repair" if needs_repair else "proposal_rejected",
        sample_registry_hash=registry.get("registry_hash"),
        base_payload={"summary": summary},
        candidate_payload={
            "recommended_actions": actions,
            "production_mutation": False,
            "artifact_mutation": False,
        },
        metrics={
            "strict_count": strict_count,
            "diagnostic_count": diagnostic_count,
            "rejected_count": rejected_count,
            "leakage_status_counts": leakage_counts,
            "top_exclusion_reasons": exclusion_counts,
        },
        gate_decision=gate,
        evidence={
            "total_samples": summary.get("total_samples"),
            "with_pre_match_snapshot": summary.get("with_pre_match_snapshot"),
            "with_prediction_snapshot": summary.get("with_prediction_snapshot"),
            "with_process_eval": summary.get("with_process_eval"),
            "source_result_conflicts": summary.get("source_result_conflicts"),
        },
        notes="Proposal-only data quality repair; no production model setting was changed.",
    )


def persist_model_change_proposal(
    db_path: str | Path,
    proposal: ModelChangeProposalCandidate,
) -> dict[str, Any]:
    """Persist a generic model-change proposal idempotently by fingerprint."""
    _validate_proposal_status(proposal.status)
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


def update_model_change_proposal_status(
    db_path: str | Path,
    *,
    proposal_id: str,
    new_status: str,
    reviewed_by: str,
    review_note: str = "",
    manual_approval: bool = False,
) -> dict[str, Any]:
    """Update proposal review status without mutating production configuration.

    ``approved_for_shadow`` and ``promoted_config`` require explicit manual
    approval metadata.  Even ``promoted_config`` only updates the proposal
    ledger; callers must perform any production config change in a separate,
    audited operation.
    """
    _validate_proposal_status(new_status)
    if new_status in MANUAL_APPROVAL_STATUSES and not manual_approval:
        raise ValueError(f"{new_status} requires manual_approval=True")
    if not reviewed_by.strip():
        raise ValueError("reviewed_by is required")

    path = Path(db_path)
    conn = sqlite3.connect(str(path))
    try:
        _require_table(conn)
        row = conn.execute(
            """
            SELECT id, status, evidence
            FROM model_change_proposals
            WHERE id=?
            """,
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Proposal not found: {proposal_id}")

        evidence = _loads(row[2])
        history = list(evidence.get("review_history") or [])
        history.append(
            {
                "from_status": row[1],
                "to_status": new_status,
                "reviewed_by": reviewed_by,
                "review_note": review_note,
                "manual_approval": manual_approval,
                "production_mutation": False,
            }
        )
        evidence["review_history"] = history
        evidence["latest_review"] = history[-1]
        conn.execute(
            """
            UPDATE model_change_proposals
            SET status=?, evidence=?
            WHERE id=?
            """,
            (new_status, _json(evidence), proposal_id),
        )
        conn.commit()
        return {
            "updated": True,
            "id": proposal_id,
            "old_status": row[1],
            "new_status": new_status,
            "production_mutation": False,
        }
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


def _proposal_type_for_candidate(candidate_name: str) -> str:
    lowered = candidate_name.lower()
    if "dirichlet" in lowered or "calibration" in lowered:
        return "calibrator"
    if "stacking" in lowered:
        return "stacking"
    if "weight" in lowered and "bayesian_weighted" not in lowered:
        return "weights"
    return "model"


def _count_values(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _exclusion_reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for reason in row.get("exclusion_reasons") or []:
            text = str(reason)
            counts[text] = counts.get(text, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _registry_repair_actions(
    summary: dict[str, Any],
    leakage_counts: dict[str, int],
    exclusion_counts: dict[str, int],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if exclusion_counts.get("missing_pre_match_snapshot", 0) > 0:
        actions.append(
            {
                "action": "backfill_or_import_pre_match_snapshots",
                "reason": "missing_pre_match_snapshot",
                "affected_samples": exclusion_counts["missing_pre_match_snapshot"],
            }
        )
    if exclusion_counts.get("snapshot_or_kickoff_time_unknown", 0) > 0:
        actions.append(
            {
                "action": "normalize_snapshot_and_kickoff_timestamps",
                "reason": "snapshot_or_kickoff_time_unknown",
                "affected_samples": exclusion_counts["snapshot_or_kickoff_time_unknown"],
            }
        )
    if exclusion_counts.get("missing_current_probabilities", 0) > 0:
        actions.append(
            {
                "action": "materialize_current_probabilities_from_valid_snapshots",
                "reason": "missing_current_probabilities",
                "affected_samples": exclusion_counts["missing_current_probabilities"],
            }
        )
    if leakage_counts.get("post_kickoff_snapshot", 0) > 0:
        actions.append(
            {
                "action": "exclude_or_rebuild_post_kickoff_snapshots",
                "reason": "post_kickoff_snapshot",
                "affected_samples": leakage_counts["post_kickoff_snapshot"],
            }
        )
    if int(summary.get("source_result_conflicts", 0) or 0) > 0:
        actions.append(
            {
                "action": "reconcile_conflicting_result_sources",
                "reason": "source_result_conflicts",
                "affected_samples": int(summary.get("source_result_conflicts", 0) or 0),
            }
        )
    if int(summary.get("with_process_eval", 0) or 0) < int(summary.get("strict_count", 0) or 0):
        actions.append(
            {
                "action": "backfill_process_evaluation_for_strict_samples",
                "reason": "process_eval_coverage_below_strict_samples",
                "affected_samples": int(summary.get("strict_count", 0) or 0)
                - int(summary.get("with_process_eval", 0) or 0),
            }
        )
    return actions


def _require_table(conn: sqlite3.Connection) -> None:
    if not _has_table(conn, "model_change_proposals"):
        raise RuntimeError("Missing model_change_proposals table; run Alembic upgrade first")


def _validate_proposal_status(status: str) -> None:
    if status not in ALLOWED_PROPOSAL_STATUSES:
        raise ValueError(f"Unsupported proposal status: {status}")


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _find_existing_by_fingerprint(conn: sqlite3.Connection, fingerprint: str) -> tuple[Any, ...] | None:
    for row in conn.execute(
        """
        SELECT
            id, proposal_type, candidate_name, source, status,
            sample_registry_hash, target_table, target_key,
            base_payload, candidate_payload, metrics, gate_decision,
            evidence
        FROM model_change_proposals
        """
    ):
        try:
            evidence = json.loads(row[12] or "{}")
        except (TypeError, json.JSONDecodeError):
            evidence = {}
        if evidence.get("fingerprint") == fingerprint:
            return row
        row_payload = {
            "proposal_type": row[1],
            "candidate_name": row[2],
            "source": row[3],
            "status": row[4],
            "sample_registry_hash": row[5],
            "target_table": row[6],
            "target_key": row[7],
            "base_payload": _loads(row[8]),
            "candidate_payload": _loads(row[9]),
            "metrics": _loads(row[10]),
            "gate_decision": _loads(row[11]),
            "evidence": evidence,
        }
        blob = json.dumps(_fingerprint_payload(row_payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(blob.encode("utf-8")).hexdigest() == fingerprint:
            return row
    return None


def _fingerprint_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.pop("notes", None)
    for key in ("base_payload", "candidate_payload", "metrics", "gate_decision", "evidence"):
        if normalized.get(key) is None:
            normalized[key] = {}
    candidate_payload = dict(normalized.get("candidate_payload") or {})
    candidate_payload.pop("experiment_id", None)
    if candidate_payload.get("candidate_family") is None:
        candidate_payload.pop("candidate_family", None)
    normalized["candidate_payload"] = candidate_payload
    evidence = dict(normalized.get("evidence") or {})
    evidence.pop("fingerprint", None)
    normalized["evidence"] = evidence
    return normalized


def _loads(value: Any) -> dict[str, Any]:
    try:
        loaded = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)
