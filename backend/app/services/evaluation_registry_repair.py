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

HARD_BLOCK_REASONS = {
    "snapshot_after_kickoff",
    "result_conflict_between_sources",
    "missing_canonical_result",
}

ACTION_PRIORITY: dict[str, str] = {
    "reconcile_conflicting_result_sources": "P0",
    "verify_final_score": "P0",
    "normalize_snapshot_and_kickoff_time": "P0",
    "import_real_pre_match_snapshot": "P0",
    "recover_current_probabilities_from_valid_snapshot": "P0",
    "exclude_or_rebuild_from_true_pre_kickoff_snapshot": "P1",
    "manual_registry_review": "P1",
}

ACTION_REPAIR_ORDER: dict[str, int] = {
    "reconcile_conflicting_result_sources": 10,
    "verify_final_score": 20,
    "normalize_snapshot_and_kickoff_time": 30,
    "import_real_pre_match_snapshot": 40,
    "recover_current_probabilities_from_valid_snapshot": 50,
    "exclude_or_rebuild_from_true_pre_kickoff_snapshot": 60,
    "manual_registry_review": 90,
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
    action_counts = Counter(action["action"] for row in rows for action in row["recommended_actions"])
    status_counts = Counter(row["sample_status"] for row in rows)
    priority_counts = Counter(row["priority"] for row in rows)
    blocking_counts = Counter(row["blocking_level"] for row in rows)
    return {
        "schema_version": "evaluation_registry_repair_report.v2",
        "registry_hash": registry["registry_hash"],
        "competition": competition,
        "summary": registry["summary"],
        "repair_summary": {
            "reported_samples": len(rows),
            "sample_status_counts": dict(status_counts),
            "priority_counts": dict(priority_counts),
            "blocking_level_counts": dict(blocking_counts),
            "action_counts": dict(action_counts),
            "action_groups": _action_groups(rows),
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
    can_promote = bool(actions) and not any(reason in HARD_BLOCK_REASONS for reason in reasons)
    if row.get("sample_status") == "strict":
        can_promote = False
    priority = _row_priority(actions)
    blocking_level = _blocking_level(row.get("sample_status"), reasons)
    repair_order = min(
        (int(action.get("repair_order", 90)) for action in actions),
        default=0 if row.get("sample_status") == "strict" else 90,
    )
    return {
        "sample_id": row["sample_id"],
        "sample_status": row["sample_status"],
        "priority": priority,
        "blocking_level": blocking_level,
        "repair_order": repair_order,
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
        "promotability_reason": _promotability_reason(row.get("sample_status"), reasons, can_promote),
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
    action = template["action"]
    return {
        "reason": reason,
        "action": action,
        "priority": ACTION_PRIORITY.get(action, "P1"),
        "blocking_level": "hard_block" if reason in HARD_BLOCK_REASONS else "repairable",
        "repair_order": str(ACTION_REPAIR_ORDER.get(action, 90)),
        "evidence_required": template["evidence_required"],
        "allowed_repair": "real_pre_kickoff_evidence_only",
    }


def _row_priority(actions: list[dict[str, str]]) -> str:
    if not actions:
        return "done"
    priorities = [action.get("priority", "P1") for action in actions]
    if "P0" in priorities:
        return "P0"
    if "P1" in priorities:
        return "P1"
    return "P2"


def _blocking_level(sample_status: str | None, reasons: list[str]) -> str:
    if sample_status == "strict":
        return "none"
    if any(reason in HARD_BLOCK_REASONS for reason in reasons):
        return "hard_block"
    if reasons:
        return "repairable"
    return "manual_review"


def _promotability_reason(sample_status: str | None, reasons: list[str], can_promote: bool) -> str:
    if sample_status == "strict":
        return "already_strict"
    hard = [reason for reason in reasons if reason in HARD_BLOCK_REASONS]
    if hard:
        return "hard_blocked_by_" + ",".join(sorted(hard))
    if can_promote:
        return "requires_real_pre_kickoff_probability_and_timestamp_evidence"
    return "no_repair_action_identified"


def _action_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        for action in row["recommended_actions"]:
            action_name = action["action"]
            group = groups.setdefault(
                action_name,
                {
                    "action": action_name,
                    "priority": action.get("priority", "P1"),
                    "blocking_level": action.get("blocking_level", "repairable"),
                    "repair_order": int(action.get("repair_order", 90)),
                    "sample_count": 0,
                    "sample_ids": [],
                    "evidence_required": action.get("evidence_required"),
                    "allowed_repair": action.get("allowed_repair"),
                },
            )
            group["sample_count"] += 1
            group["sample_ids"].append(row["sample_id"])
    return sorted(groups.values(), key=lambda item: (item["repair_order"], item["action"]))
