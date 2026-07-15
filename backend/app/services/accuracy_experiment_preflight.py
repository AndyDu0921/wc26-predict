"""Read-only preflight checks for shadow accuracy experiments."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.db_integrity_audit import audit_sqlite_integrity
from app.services.evaluation_registry import WC26_COMPETITION, build_evaluation_registry
from app.services.evaluation_registry_repair import build_evaluation_registry_repair_report


def run_accuracy_experiment_preflight(
    db_path: str | Path,
    *,
    competition: str = WC26_COMPETITION,
    min_sample_count: int = 30,
    candidates: list[str] | None = None,
    required_model_cohort: str | None = None,
) -> dict[str, Any]:
    """Return gate evidence before running candidate tournaments.

    The preflight is intentionally conservative and read-only.  It blocks
    experiments when the paired strict sample pool is too small or when the
    database integrity gate is not green.  Non-strict repair work is surfaced
    as warnings so it stays visible without fabricating evaluation samples.
    """
    db_audit = audit_sqlite_integrity(db_path)
    registry = build_evaluation_registry(db_path, competition=competition)
    repair = build_evaluation_registry_repair_report(db_path, competition=competition)

    summary = registry["summary"]
    strict_count = int(summary.get("strict_count", 0) or 0)
    eligible_count = int(summary.get("eligible_backtest_count", 0) or 0)
    cohort_count = int(
        (summary.get("strict_model_cohort_counts") or {}).get(required_model_cohort, 0)
        if required_model_cohort
        else strict_count
    )
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if db_audit["integrity_check"] != "ok":
        blockers.append(
            {
                "code": "sqlite_integrity_check_failed",
                "evidence": {"integrity_check": db_audit["integrity_check"]},
                "required_action": "Repair or restore the database before running experiments.",
            }
        )
    if int(db_audit["foreign_key_violation_count"] or 0) > 0:
        blockers.append(
            {
                "code": "foreign_key_drift_present",
                "evidence": {
                    "foreign_key_violation_count": db_audit["foreign_key_violation_count"],
                    "violation_counts_by_table": db_audit.get("violation_counts_by_table"),
                },
                "required_action": "Run the DB integrity audit repair workflow with quarantine evidence.",
            }
        )
    if strict_count < min_sample_count:
        blockers.append(
            {
                "code": "insufficient_strict_samples",
                "evidence": {"strict_count": strict_count, "min_sample_count": min_sample_count},
                "required_action": "Promote only real pre-kickoff timestamped samples into strict evaluation.",
            }
        )
    if eligible_count < min_sample_count:
        blockers.append(
            {
                "code": "insufficient_eligible_samples",
                "evidence": {"eligible_count": eligible_count, "min_sample_count": min_sample_count},
                "required_action": "Do not run paired candidate gates until enough no-leakage samples exist.",
            }
        )
    if required_model_cohort and cohort_count < min_sample_count:
        blockers.append(
            {
                "code": "insufficient_model_cohort_samples",
                "evidence": {
                    "required_model_cohort": required_model_cohort,
                    "cohort_count": cohort_count,
                    "min_sample_count": min_sample_count,
                    "strict_model_cohort_counts": summary.get("strict_model_cohort_counts", {}),
                },
                "required_action": (
                    "Keep candidates in shadow until the active model cohort has enough "
                    "independent no-leakage outcomes. Do not pool legacy champions for promotion."
                ),
            }
        )

    source_conflicts = int(summary.get("source_result_conflicts", 0) or 0)
    if source_conflicts:
        warnings.append(
            {
                "code": "result_source_conflicts_present",
                "evidence": {"source_result_conflicts": source_conflicts},
                "recommended_action": "Resolve conflicts before using those matches in any strict sample repair.",
            }
        )
    repair_summary = repair["repair_summary"]
    if int(repair_summary.get("potentially_promotable_count", 0) or 0) > 0:
        warnings.append(
            {
                "code": "repairable_diagnostic_samples_present",
                "evidence": {
                    "potentially_promotable_count": repair_summary.get("potentially_promotable_count"),
                    "action_counts": repair_summary.get("action_counts"),
                },
                "recommended_action": "Use only real pre-kickoff evidence to repair diagnostic rows.",
            }
        )

    passed = not blockers
    return {
        "schema_version": "accuracy_experiment_preflight.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "competition": competition,
        "candidate_names": candidates or [],
        "min_sample_count": min_sample_count,
        "required_model_cohort": required_model_cohort,
        "required_model_cohort_sample_count": cohort_count,
        "status": "ready" if passed else "blocked",
        "passed": passed,
        "blockers": blockers,
        "warnings": warnings,
        "registry_hash": registry["registry_hash"],
        "registry_summary": summary,
        "repair_summary": repair_summary,
        "db_integrity": {
            "integrity_check": db_audit["integrity_check"],
            "foreign_key_violation_count": db_audit["foreign_key_violation_count"],
            "affected_row_count": db_audit["affected_row_count"],
        },
        "notes": (
            "Read-only preflight. It does not create snapshots, probabilities, "
            "production weights, artifacts, or reports."
        ),
    }
