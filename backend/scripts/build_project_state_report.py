#!/usr/bin/env python3
"""Build a read-only project state report for AI handoff and audit.

The report is the machine-readable source of current project facts. It reads
the local SQLite database, reports, memory files, and lightweight audit helpers;
it never changes model weights, historical probabilities, artifacts, or match
results.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_OUTPUT = REPO_ROOT / "reports" / "audits" / "current_project_state.json"
sys.path.insert(0, str(BACKEND_DIR))

from app.services.evaluation_registry import (  # noqa: E402
    DEFAULT_DB_PATH,
    WC26_COMPETITION,
    build_evaluation_registry,
)
from app.version import BUILD_NAME, VERSION, get_git_commit  # noqa: E402


FINISHED_STATUSES = {"FINISHED", "Finished", "finished", "FT", "Final", "full_time"}
PREMATCH_REQUIRED = (
    "pre_match_snapshots",
    "prediction_snapshots",
    "prediction_runs",
    "feature_snapshots",
    "prediction_report",
    "evidence_items",
)
POSTMATCH_REQUIRED = (
    "match_results",
    "match_team_statistics",
    "postmatch_process_eval",
    "prediction_learning_log",
    "postmatch_eval",
    "signal_evaluations",
    "postmatch_report",
    "postmatch_memory",
)


def build_project_state_report(
    db_path: str | Path = DEFAULT_DB_PATH,
    *,
    repo_root: str | Path = REPO_ROOT,
    include_all_matches: bool = False,
    include_accuracy: bool = True,
    include_db_integrity: bool = True,
) -> dict[str, Any]:
    """Return a read-only project state report."""
    db_path = Path(db_path)
    repo_root = Path(repo_root)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        schedule = _load_schedule(conn)
        match_states = [
            _build_match_state(conn, repo_root=repo_root, row=row)
            for row in schedule
            if include_all_matches or str(row["stage"]).lower() != "group stage"
        ]
        all_match_states = [
            _build_match_state(conn, repo_root=repo_root, row=row)
            for row in schedule
        ]
        stage_summary = _stage_summary(all_match_states)
        registry_payload = _accuracy_payload(db_path) if include_accuracy else _skipped("disabled")
        integrity_payload = _db_integrity_payload(db_path) if include_db_integrity else _skipped("disabled")
        table_counts = _table_counts(conn)
        report = {
            "schema_version": "project_state_report.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "repo_root": str(repo_root),
            "db_path": str(db_path),
            "version": {
                "version": VERSION,
                "build_name": BUILD_NAME,
                "git_commit": get_git_commit(),
                "git_status": _git_status(repo_root),
            },
            "fact_source_policy": {
                "primary_sources": [
                    "SQLite DB tables",
                    "stored report and memory files",
                    "evidence_items / match_data_raw raw ledgers",
                    "project audit scripts",
                ],
                "do_not_use_as_authority": [
                    "compressed chat summaries",
                    "uncited AI memory",
                    "single web-search snippets",
                    "unpersisted browser summaries",
                ],
                "postmatch_boundary": (
                    "Post-match official data can support reviews and learning logs, "
                    "but must not be joined into same-match pre-match strict features."
                ),
            },
            "database": {
                "exists": db_path.exists(),
                "table_counts": table_counts,
                "integrity": integrity_payload,
            },
            "model_runtime": _artifact_bundle_payload(repo_root),
            "accuracy_os": registry_payload,
            "competition_state": {
                "competition": WC26_COMPETITION,
                "schedule_rows": len(schedule),
                "stage_summary": stage_summary,
                "tracked_match_count": len(match_states),
                "tracked_matches_scope": "all_matches" if include_all_matches else "non_group_stage",
                "tracked_matches": match_states,
            },
            "operational_state": _operational_state(conn),
            "known_risks": [],
            "recommended_next_actions": [],
            "handoff_checklist": _handoff_checklist(),
        }
        report["known_risks"] = _known_risks(report)
        report["recommended_next_actions"] = _recommended_next_actions(report)
        return report
    finally:
        conn.close()


def _load_schedule(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    if not _has_table(conn, "wc26_schedule"):
        return []
    return list(
        conn.execute(
            """
            SELECT id, match_number, home_slot, away_slot, stage, group_name,
                   match_date, kickoff_time, venue, city, home_team, away_team,
                   home_goals, away_goals, match_status
            FROM wc26_schedule
            ORDER BY match_number, id
            """
        )
    )


def _build_match_state(conn: sqlite3.Connection, *, repo_root: Path, row: sqlite3.Row) -> dict[str, Any]:
    match_id = str(row["id"])
    status = str(row["match_status"] or "")
    is_finished = status in FINISHED_STATUSES
    counts = {
        "pre_match_snapshots": _count_match(conn, "pre_match_snapshots", match_id),
        "prediction_snapshots": _count_match(conn, "prediction_snapshots", match_id),
        "prediction_runs": _count_match(conn, "prediction_runs", match_id),
        "feature_snapshots": _count_match(conn, "feature_snapshots", match_id),
        "evidence_items": _count_match(conn, "evidence_items", match_id),
        "information_state_signals": _count_match(conn, "information_state_signals", match_id),
        "signal_evaluations": _count_match(conn, "signal_evaluations", match_id),
        "match_results": _count_match(conn, "match_results", match_id),
        "match_team_statistics": _count_match(conn, "match_team_statistics", match_id),
        "postmatch_process_eval": _count_match(conn, "postmatch_process_eval", match_id),
        "prediction_learning_log": _count_match(conn, "prediction_learning_log", match_id),
        "postmatch_eval": _count_postmatch_eval(conn, match_id),
        "match_data_raw": _count_match(conn, "match_data_raw", match_id),
        "match_events": _count_match(conn, "match_events", match_id),
        "match_game_state_segments": _count_match(conn, "match_game_state_segments", match_id),
    }
    prediction_report_path = _latest_prediction_report_path(conn, match_id)
    prediction_report = {
        "path": prediction_report_path,
        "exists": _path_exists(repo_root, prediction_report_path),
    }
    postmatch_report = _postmatch_report(repo_root, row)
    postmatch_memory = _postmatch_memory(repo_root, row)
    checks = {
        "pre_match_snapshots": counts["pre_match_snapshots"] > 0,
        "prediction_snapshots": counts["prediction_snapshots"] > 0,
        "prediction_runs": counts["prediction_runs"] > 0,
        "feature_snapshots": counts["feature_snapshots"] > 0,
        "prediction_report": prediction_report["exists"],
        "evidence_items": counts["evidence_items"] > 0,
        "match_results": counts["match_results"] > 0,
        "match_team_statistics": counts["match_team_statistics"] >= 2,
        "postmatch_process_eval": counts["postmatch_process_eval"] > 0,
        "prediction_learning_log": counts["prediction_learning_log"] > 0,
        "postmatch_eval": counts["postmatch_eval"] > 0,
        "signal_evaluations": counts["signal_evaluations"] > 0,
        "postmatch_report": postmatch_report["exists"],
        "postmatch_memory": postmatch_memory["exists"],
    }
    pre_missing = [name for name in PREMATCH_REQUIRED if not checks.get(name, False)]
    post_missing = [name for name in POSTMATCH_REQUIRED if not checks.get(name, False)]
    pre_snapshot_present = bool(
        counts["pre_match_snapshots"]
        or counts["prediction_snapshots"]
        or counts["prediction_runs"]
        or counts["feature_snapshots"]
    )
    postmatch_review_present = bool(
        counts["prediction_learning_log"]
        or counts["postmatch_process_eval"]
        or postmatch_report["exists"]
        or postmatch_memory["exists"]
    )
    return {
        "match_id": match_id,
        "match_number": row["match_number"],
        "stage": row["stage"],
        "match_date": row["match_date"],
        "kickoff_time": row["kickoff_time"],
        "venue": row["venue"],
        "city": row["city"],
        "home_team": row["home_team"],
        "away_team": row["away_team"],
        "score": {
            "home_goals": row["home_goals"],
            "away_goals": row["away_goals"],
            "status": status,
            "is_finished": is_finished,
            "result_label": _result_label(row["home_goals"], row["away_goals"], is_finished),
        },
        "slots": {
            "home_slot": row["home_slot"],
            "away_slot": row["away_slot"],
            "teams_known": bool(row["home_team"] and row["away_team"]),
        },
        "counts": counts,
        "checks": checks,
        "pre_match_snapshot_present": pre_snapshot_present,
        "v410_pre_match_complete": not pre_missing,
        "pre_match_missing": pre_missing,
        "postmatch_review_present": is_finished and postmatch_review_present,
        "v410_postmatch_complete": is_finished and not post_missing,
        "postmatch_missing": post_missing if is_finished else [],
        "prediction_report": prediction_report,
        "postmatch_report": postmatch_report,
        "postmatch_memory": postmatch_memory,
        "rich_postmatch": _rich_postmatch_summary(conn, match_id),
    }


def _stage_summary(match_states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order: dict[str, int] = {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in match_states:
        stage = str(item["stage"])
        order.setdefault(stage, int(item["match_number"] or 0))
        grouped.setdefault(stage, []).append(item)
    summary = []
    for stage in sorted(grouped, key=lambda key: order[key]):
        rows = grouped[stage]
        summary.append(
            {
                "stage": stage,
                "total": len(rows),
                "finished": sum(1 for row in rows if row["score"]["is_finished"]),
                "scheduled": sum(1 for row in rows if not row["score"]["is_finished"]),
                "teams_known": sum(1 for row in rows if row["slots"]["teams_known"]),
                "empty_team_slots": sum(1 for row in rows if not row["slots"]["teams_known"]),
                "pre_match_snapshot_present": sum(1 for row in rows if row["pre_match_snapshot_present"]),
                "postmatch_review_present": sum(1 for row in rows if row["postmatch_review_present"]),
                "v410_pre_match_complete": sum(1 for row in rows if row["v410_pre_match_complete"]),
                "v410_postmatch_complete": sum(1 for row in rows if row["v410_postmatch_complete"]),
            }
        )
    return summary


def _accuracy_payload(db_path: Path) -> dict[str, Any]:
    try:
        registry = build_evaluation_registry(db_path)
        return {
            "status": "ok",
            "registry_hash": registry["registry_hash"],
            "summary": registry["summary"],
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _db_integrity_payload(db_path: Path) -> dict[str, Any]:
    try:
        from app.services.db_integrity_audit import audit_sqlite_integrity

        payload = audit_sqlite_integrity(db_path)
        return {
            "status": "ok",
            "integrity_check": payload.get("integrity_check"),
            "foreign_key_violation_count": payload.get("foreign_key_violation_count"),
            "affected_row_count": payload.get("affected_row_count"),
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _operational_state(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "evidence_items": _count_table(conn, "evidence_items"),
        "information_state_signals": _count_table(conn, "information_state_signals"),
        "signal_evaluations": _count_table(conn, "signal_evaluations"),
        "model_change_proposals": _count_table(conn, "model_change_proposals"),
        "model_weight_proposals": _count_table(conn, "model_weight_proposals"),
        "match_data_raw": _count_table(conn, "match_data_raw"),
        "match_events": _count_table(conn, "match_events"),
        "match_game_state_segments": _count_table(conn, "match_game_state_segments"),
    }


def _artifact_bundle_payload(repo_root: Path) -> dict[str, Any]:
    manifest = repo_root / "backend" / "artifacts" / "active_bundle.json"
    if not manifest.is_file():
        return {"status": "missing", "path": str(manifest)}
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "path": str(manifest), "error": str(exc)}
    components = payload.get("components") if isinstance(payload, dict) else None
    return {
        "status": str(payload.get("status") or "unknown"),
        "bundle_id": payload.get("bundle_id"),
        "manifest_path": str(manifest.relative_to(repo_root)).replace("\\", "/"),
        "promotion_evidence": payload.get("promotion_evidence", False),
        "training_data": payload.get("training_data", {}),
        "component_count": len(components) if isinstance(components, dict) else 0,
    }


def _known_risks(report: dict[str, Any]) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    registry = report.get("accuracy_os", {}).get("summary") or {}
    if registry.get("strict_count", 0) < 50:
        risks.append(
            {
                "code": "strict_sample_count_below_target",
                "severity": "P0",
                "evidence": {
                    "strict_count": registry.get("strict_count"),
                    "target": 50,
                    "diagnostic_count": registry.get("diagnostic_count"),
                },
            }
        )
    if registry:
        cohort_count = int(
            (registry.get("strict_model_cohort_counts") or {}).get(VERSION, 0)
        )
        if cohort_count < 30:
            risks.append(
                {
                    "code": "current_version_cohort_below_minimum",
                    "severity": "P0",
                    "evidence": {
                        "model_cohort": VERSION,
                        "strict_count": cohort_count,
                        "engineering_minimum": 30,
                        "preferred_target": 50,
                    },
                    "note": "No current-version predictive improvement may be claimed.",
                }
            )
    runtime = report.get("model_runtime") or {}
    training_data = runtime.get("training_data") or {}
    if runtime.get("status") != "promoted" or not training_data.get("provenance_complete"):
        risks.append(
            {
                "code": "active_artifact_provenance_incomplete",
                "severity": "P0",
                "evidence": {
                    "bundle_id": runtime.get("bundle_id"),
                    "status": runtime.get("status"),
                    "training_cutoff": training_data.get("cutoff"),
                    "provenance_complete": bool(training_data.get("provenance_complete")),
                },
                "note": "Hash integrity is not equivalent to temporal training provenance.",
            }
        )
    for stage in report["competition_state"]["stage_summary"]:
        if stage["empty_team_slots"]:
            risks.append(
                {
                    "code": "scheduled_stage_has_empty_team_slots",
                    "severity": _empty_slot_severity(stage),
                    "evidence": {
                        "stage": stage["stage"],
                        "empty_team_slots": stage["empty_team_slots"],
                        "scheduled": stage["scheduled"],
                    },
                }
            )
    incomplete_finished = [
        {
            "match_id": row["match_id"],
            "stage": row["stage"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "missing": row["postmatch_missing"],
        }
        for row in report["competition_state"]["tracked_matches"]
        if row["score"]["is_finished"] and row["postmatch_missing"]
    ]
    if incomplete_finished:
        by_stage: dict[str, int] = {}
        for item in incomplete_finished:
            by_stage[item["stage"]] = by_stage.get(item["stage"], 0) + 1
        risks.append(
            {
                "code": "finished_tracked_matches_missing_v410_postmatch_fields",
                "severity": "P1",
                "evidence": {
                    "count": len(incomplete_finished),
                    "by_stage": by_stage,
                    "examples": incomplete_finished[:8],
                },
                "note": (
                    "This does not mean the match was never reviewed; it means one or more "
                    "V4.10+ closed-loop fields are absent."
                ),
            }
        )
    return risks


def _empty_slot_severity(stage: dict[str, Any]) -> str:
    if stage["stage"] == "Quarterfinal" and stage["scheduled"] > 0:
        return "P0"
    if stage["stage"] in {"Semifinal", "Final"}:
        return "P2"
    return "P1"


def _recommended_next_actions(report: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = [
        {
            "priority": "P0",
            "action": "Before any AI work, read this JSON and docs/AI_HANDOFF_PROTOCOL.md.",
        }
    ]
    for risk in report["known_risks"]:
        if risk["code"] == "scheduled_stage_has_empty_team_slots":
            actions.append(
                {
                    "priority": risk["severity"],
                    "action": (
                        "Resolve empty team slots from verified project DB/bracket evidence before "
                        "running predictions for that stage."
                    ),
                    "evidence": risk["evidence"],
                }
            )
        elif risk["code"] == "strict_sample_count_below_target":
            actions.append(
                {
                    "priority": "P0",
                    "action": (
                        "Repair diagnostic registry rows only from real pre-kickoff snapshots; "
                        "do not fabricate probabilities or timestamps."
                    ),
                    "evidence": risk["evidence"],
                }
            )
        elif risk["code"] == "current_version_cohort_below_minimum":
            actions.append(
                {
                    "priority": "P0",
                    "action": (
                        "Keep the current version frozen and collect same-cohort temporal outcomes; "
                        "do not use pooled historical metrics as promotion evidence."
                    ),
                    "evidence": risk["evidence"],
                }
            )
        elif risk["code"] == "active_artifact_provenance_incomplete":
            actions.append(
                {
                    "priority": "P0",
                    "action": (
                        "Train the next shadow bundle with exact cutoff and row fingerprint, then "
                        "promote only after same-cohort paired gates and manual approval."
                    ),
                    "evidence": risk["evidence"],
                }
            )
    return actions


def _handoff_checklist() -> list[str]:
    return [
        "Read docs/CURRENT_PROJECT_STATE.md and reports/audits/current_project_state.json.",
        "Run build_project_state_report.py before making tournament-state claims.",
        "Treat DB/evidence/report artifacts as facts; treat chat summaries as hints only.",
        "Use official provider adapters for FIFA data when possible; do not rely on uncited web snippets.",
        "Do not alter historical predictions, production weights, or artifacts without an explicit request.",
        "For completed matches, verify closed-loop tables before rerunning postmatch work.",
        "State the model cohort and sample size before quoting any accuracy metric.",
        "Verify active artifact hashes and training provenance separately.",
    ]


def _rich_postmatch_summary(conn: sqlite3.Connection, match_id: str) -> dict[str, Any]:
    raw = _count_match(conn, "match_data_raw", match_id)
    events = _count_match(conn, "match_events", match_id)
    segments = _count_match(conn, "match_game_state_segments", match_id)
    if not raw and not events and not segments:
        return {"available": False, "tier": "none", "raw": raw, "events": events, "segments": segments}
    return {
        "available": bool(events or segments),
        "tier": "event_timeline_present" if events else "raw_only",
        "raw": raw,
        "events": events,
        "segments": segments,
    }


def _result_label(home_goals: Any, away_goals: Any, is_finished: bool) -> str | None:
    if not is_finished or home_goals is None or away_goals is None:
        return None
    if home_goals > away_goals:
        return "home"
    if away_goals > home_goals:
        return "away"
    return "draw_or_tiebreak_required"


def _latest_prediction_report_path(conn: sqlite3.Connection, match_id: str) -> str | None:
    if not _has_table(conn, "prediction_snapshots"):
        return None
    row = conn.execute(
        """
        SELECT report_path
        FROM prediction_snapshots
        WHERE CAST(match_id AS TEXT)=?
          AND report_path IS NOT NULL
          AND report_path <> ''
        ORDER BY generated_at DESC, id DESC
        LIMIT 1
        """,
        (match_id,),
    ).fetchone()
    return str(row["report_path"]) if row and row["report_path"] else None


def _postmatch_report(repo_root: Path, row: sqlite3.Row) -> dict[str, Any]:
    home = row["home_team"]
    away = row["away_team"]
    if not home or not away:
        return {"exists": False, "path": None}
    reports_dir = repo_root / "reports" / "postmatch"
    matches = sorted(reports_dir.glob(f"*_{_token(home, '_')}_{_token(away, '_')}_postmatch.md"))
    return {
        "exists": bool(matches),
        "path": _rel(repo_root, matches[-1]) if matches else None,
    }


def _postmatch_memory(repo_root: Path, row: sqlite3.Row) -> dict[str, Any]:
    home = row["home_team"]
    away = row["away_team"]
    if not home or not away:
        return {"exists": False, "path": None}
    memory_dir = repo_root / "memory"
    matches = sorted(memory_dir.glob(f"wc-postmatch-{_token(home, '')}-{_token(away, '')}-*.md"))
    return {
        "exists": bool(matches),
        "path": _rel(repo_root, matches[-1]) if matches else None,
    }


def _token(value: str, separator: str) -> str:
    return str(value).replace(" ", separator)


def _rel(repo_root: Path, path: Path) -> str:
    return str(path.relative_to(repo_root)).replace("\\", "/")


def _path_exists(repo_root: Path, rel_path: str | None) -> bool:
    if not rel_path:
        return False
    path = Path(rel_path)
    if path.is_absolute():
        return path.exists()
    return (repo_root / rel_path.replace("\\", "/")).exists()


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not _has_table(conn, table_name):
        return set()
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _count_table(conn: sqlite3.Connection, table_name: str) -> int:
    if not _has_table(conn, table_name):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _count_match(conn: sqlite3.Connection, table_name: str, match_id: str) -> int:
    if "match_id" not in _columns(conn, table_name):
        return 0
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM {table_name} WHERE CAST(match_id AS TEXT)=?",
        (match_id,),
    ).fetchone()
    return int(row["c"] if row else 0)


def _count_postmatch_eval(conn: sqlite3.Connection, match_id: str) -> int:
    if not _has_table(conn, "postmatch_eval"):
        return 0
    run_ids: set[str] = set()
    if _has_table(conn, "prediction_runs"):
        for row in conn.execute(
                """
                SELECT pe.prediction_run_id
                FROM postmatch_eval pe
                JOIN prediction_runs pr ON pe.prediction_run_id = pr.id
                WHERE CAST(pr.match_id AS TEXT)=?
                """,
                (match_id,),
        ):
            run_ids.add(str(row["prediction_run_id"]))
    if _has_table(conn, "prediction_learning_log"):
        for row in conn.execute(
                """
                SELECT pe.prediction_run_id
                FROM postmatch_eval pe
                JOIN prediction_learning_log pll
                  ON pe.prediction_run_id = pll.prediction_run_id
                WHERE CAST(pll.match_id AS TEXT)=?
                """,
                (match_id,),
        ):
            run_ids.add(str(row["prediction_run_id"]))
    return len(run_ids)


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [
        "wc26_schedule",
        "prediction_runs",
        "pre_match_snapshots",
        "prediction_snapshots",
        "feature_snapshots",
        "evidence_items",
        "information_state_signals",
        "match_results",
        "postmatch_process_eval",
        "prediction_learning_log",
        "postmatch_eval",
        "signal_evaluations",
        "match_data_raw",
        "match_events",
        "match_game_state_segments",
        "model_change_proposals",
        "model_weight_proposals",
    ]
    return {table: _count_table(conn, table) for table in tables}


def _git_status(repo_root: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return {
        "available": result.returncode == 0,
        "dirty": bool(lines),
        "changed_file_count": len(lines),
    }


def _skipped(reason: str) -> dict[str, Any]:
    return {"status": "skipped", "reason": reason}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--include-all-matches", action="store_true")
    parser.add_argument("--skip-accuracy", action="store_true")
    parser.add_argument("--skip-db-integrity", action="store_true")
    parser.add_argument("--print", action="store_true", dest="print_json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = build_project_state_report(
        args.db_path,
        include_all_matches=args.include_all_matches,
        include_accuracy=not args.skip_accuracy,
        include_db_integrity=not args.skip_db_integrity,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    if args.print_json or not args.output:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
