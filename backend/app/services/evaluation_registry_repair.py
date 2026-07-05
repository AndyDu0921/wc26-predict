"""Read-only repair planning for evaluation-registry samples."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from app.services.evaluation_registry import WC26_COMPETITION, build_evaluation_registry


ACTION_BY_REASON: dict[str, dict[str, str]] = {
    "missing_pre_match_snapshot": {
        "action": "import_real_pre_match_snapshot",
        "evidence_required": "A pre-kickoff snapshot or prediction record generated before kickoff.",
    },
    "snapshot_or_kickoff_time_unknown": {
        "action": "normalize_snapshot_and_kickoff_time",
        "evidence_required": "Verifiable kickoff_at and as_of_time timestamps.",
    },
    "missing_current_probabilities": {
        "action": "recover_current_probabilities_from_valid_snapshot",
        "evidence_required": "Pre-kickoff H/D/A probabilities from pre_match_snapshots or prediction_snapshots.",
    },
    "snapshot_after_kickoff": {
        "action": "exclude_or_rebuild_from_true_pre_kickoff_snapshot",
        "evidence_required": "A replacement snapshot whose as_of_time is before kickoff.",
    },
    "result_conflict_between_sources": {
        "action": "reconcile_conflicting_result_sources",
        "evidence_required": "Canonical final score from trusted result source(s).",
    },
    "missing_canonical_result": {
        "action": "verify_final_score",
        "evidence_required": "Canonical full-time result.",
    },
}


def build_evaluation_registry_repair_report(
    db_path: str | Path,
    *,
    competition: str = WC26_COMPETITION,
    include_strict: bool = False,
) -> dict[str, Any]:
    """Build a deterministic, read-only data repair report from registry rows."""
    registry = build_evaluation_registry(db_path, competition=competition)
    rows = [
        _repair_row(row)
        for row in registry["samples"]
        if include_strict or row["sample_status"] != "strict"
    ]
    action_counts = Counter(
        action["action"]
        for row in rows
        for action in row["recommended_actions"]
    )
    status_counts = Counter(row["sample_status"] for row in rows)
    return {
        "schema_version": "evaluation_registry_repair_report.v1",
        "registry_hash": registry["registry_hash"],
        "competition": competition,
        "summary": registry["summary"],
        "repair_summary": {
            "reported_samples": len(rows),
            "sample_status_counts": dict(status_counts),
            "action_counts": dict(action_counts),
            "potentially_promotable_count": sum(
                1 for row in rows if row["can_promote_to_strict_after_actions"]
            ),
            "must_remain_rejected_count": sum(
                1 for row in rows if not row["can_promote_to_strict_after_actions"]
            ),
        },
        "samples": rows,
        "notes": (
            "Read-only repair plan. It does not create snapshots, probabilities, "
            "results, production weights, artifacts, or reports."
        ),
    }


def _repair_row(row: dict[str, Any]) -> dict[str, Any]:
    reasons = list(row.get("exclusion_reasons") or [])
    actions = [_action_for_reason(reason) for reason in reasons]
    hard_blocks = {
        "snapshot_after_kickoff",
        "result_conflict_between_sources",
        "missing_canonical_result",
    }
    can_promote = bool(actions) and not any(reason in hard_blocks for reason in reasons)
    if row.get("sample_status") == "strict":
        can_promote = False
    return {
        "sample_id": row["sample_id"],
        "sample_status": row["sample_status"],
        "canonical_match_id": row.get("canonical_match_id"),
        "home_team": row["home_team"],
        "away_team": row["away_team"],
        "stage": row.get("stage"),
        "kickoff_at": row.get("kickoff_at"),
        "as_of_time": row.get("as_of_time"),
        "horizon_bucket": row.get("horizon_bucket"),
        "leakage_status": row.get("leakage_status"),
        "current_prob_source": row.get("current_prob_source"),
        "canonical_result_source": row.get("canonical_result_source"),
        "exclusion_reasons": reasons,
        "recommended_actions": actions,
        "evidence_sources_present": {
            "has_match_result": bool(row.get("has_match_result")),
            "has_schedule_result": bool(row.get("has_schedule_result")),
            "has_pre_match_snapshot": bool(row.get("has_pre_match_snapshot")),
            "has_prediction_snapshot": bool(row.get("has_prediction_snapshot")),
            "has_process_eval": bool(row.get("has_process_eval")),
        },
        "can_promote_to_strict_after_actions": can_promote,
        "promotion_policy": (
            "Can be promoted only after real pre-kickoff timestamped probability evidence exists."
            if can_promote
            else "Do not promote without replacing hard-blocking evidence."
        ),
    }


def _action_for_reason(reason: str) -> dict[str, str]:
    template = ACTION_BY_REASON.get(
        reason,
        {
            "action": "manual_registry_review",
            "evidence_required": "Human review of registry row and source tables.",
        },
    )
    return {
        "reason": reason,
        "action": template["action"],
        "evidence_required": template["evidence_required"],
        "allowed_repair": "real_pre_kickoff_evidence_only",
    }
