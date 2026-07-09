#!/usr/bin/env python3
"""Audit current operational entrypoints and block stale script references."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
CURRENT_ENTRYPOINTS = [
    "backend/scripts/predict_match_full.py",
    "backend/scripts/run_postmatch_complete.py",
    "backend/scripts/run_accuracy_experiments.py",
    "backend/scripts/preflight_accuracy_experiments.py",
    "backend/scripts/build_project_state_report.py",
    "backend/scripts/audit_db_integrity.py",
    "backend/scripts/audit_public_outputs.py",
    "backend/scripts/collect_match_evidence.py",
    "backend/scripts/extract_information_signals.py",
    "backend/scripts/score_information_signals.py",
    "backend/scripts/audit_match_information_state.py",
    "backend/scripts/collect_official_match_data.py",
    "backend/scripts/normalize_match_events.py",
    "backend/scripts/build_game_state_segments.py",
    "backend/scripts/audit_rich_postmatch_data.py",
]
REMOVED_ENTRYPOINTS = [
    "backend/scripts/daily_ops.py",
    "backend/scripts/audit_public_outputs_no_odds.py",
    "backend/scripts/backtest_full_pipeline.py",
    "backend/scripts/grid_search_score_params.py",
    "backend/scripts/collect_stacking_training_data.py",
    "backend/scripts/_accuracy_wrapper.py",
]
SCAN_PATHS = [
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs",
    "scripts",
    ".github",
]
SCAN_EXCLUDE_DIRS = {"archive"}


def audit_entrypoints(*, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    missing_current = [
        rel_path for rel_path in CURRENT_ENTRYPOINTS if not (repo_root / rel_path).exists()
    ]
    still_present_removed = [
        rel_path for rel_path in REMOVED_ENTRYPOINTS if (repo_root / rel_path).exists()
    ]
    stale_references = _find_stale_references(repo_root)
    passed = not missing_current and not still_present_removed and not stale_references
    return {
        "schema_version": "entrypoint_audit.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "current_entrypoints": CURRENT_ENTRYPOINTS,
        "removed_entrypoints": REMOVED_ENTRYPOINTS,
        "missing_current": missing_current,
        "still_present_removed": still_present_removed,
        "stale_references": stale_references,
    }


def _find_stale_references(repo_root: Path) -> list[dict[str, Any]]:
    needles = [Path(item).name for item in REMOVED_ENTRYPOINTS]
    findings: list[dict[str, Any]] = []
    for rel_scan_path in SCAN_PATHS:
        scan_path = repo_root / rel_scan_path
        if not scan_path.exists():
            continue
        files = [scan_path] if scan_path.is_file() else sorted(scan_path.rglob("*"))
        for path in files:
            if not path.is_file() or any(part in SCAN_EXCLUDE_DIRS for part in path.relative_to(repo_root).parts):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, 1):
                for needle in needles:
                    if needle in line:
                        findings.append(
                            {
                                "path": path.relative_to(repo_root).as_posix(),
                                "line": line_no,
                                "entrypoint": needle,
                                "context": line.strip(),
                            }
                        )
    return findings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit current operational entrypoints")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = audit_entrypoints()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["passed"]:
        print("PASS: operational entrypoints are current and stale references were not found.")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
