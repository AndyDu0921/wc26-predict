from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.audit_entrypoints import audit_entrypoints
from scripts.audit_report_paths import (
    audit_report_paths,
    build_archive_manifest,
    write_archive_manifest,
    apply_report_path_repairs,
)


def _make_prediction_snapshot_db(path: Path, report_path: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE prediction_snapshots (
                id TEXT PRIMARY KEY,
                match_id TEXT,
                report_path TEXT,
                report_markdown TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE pre_match_snapshots (
                id TEXT PRIMARY KEY,
                report_markdown TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO prediction_snapshots (id, match_id, report_path) VALUES (?, ?, ?)",
            ("snap-1", "match-1", report_path),
        )
        conn.execute(
            "INSERT INTO pre_match_snapshots (id, report_markdown) VALUES (?, ?)",
            ("pre-1", "# Report body"),
        )
        conn.commit()
    finally:
        conn.close()


def test_archive_manifest_records_files_with_checksums(tmp_path: Path):
    archive_dir = tmp_path / "reports" / "archive" / "legacy-root"
    archive_dir.mkdir(parents=True)
    (archive_dir / "old.md").write_text("legacy report", encoding="utf-8")

    manifest = build_archive_manifest(repo_root=tmp_path)

    assert manifest["file_count"] == 1
    assert manifest["files"][0]["path"] == "reports/archive/legacy-root/old.md"
    assert len(manifest["files"][0]["sha256"]) == 64


def test_report_path_audit_repairs_legacy_root_path_to_archive(tmp_path: Path):
    archive_dir = tmp_path / "reports" / "archive" / "legacy-root"
    archive_dir.mkdir(parents=True)
    (archive_dir / "old.md").write_text("legacy report", encoding="utf-8")
    db_path = tmp_path / "local.db"
    _make_prediction_snapshot_db(db_path, "reports/old.md")
    write_archive_manifest(repo_root=tmp_path)

    before = audit_report_paths(db_path=db_path, repo_root=tmp_path)
    repair = apply_report_path_repairs(
        db_path=db_path,
        repo_root=tmp_path,
        backup_dir=tmp_path / "_archive" / "db_backups",
    )
    after = audit_report_paths(db_path=db_path, repo_root=tmp_path)

    assert before["summary"]["db_report_paths"]["archive_hit_count"] == 1
    assert repair["updated_count"] == 1
    assert repair["passed_after"] is True
    assert after["summary"]["db_report_paths"]["missing_count"] == 0
    conn = sqlite3.connect(db_path)
    try:
        stored = conn.execute("SELECT report_path FROM prediction_snapshots").fetchone()[0]
    finally:
        conn.close()
    assert stored == "reports/archive/legacy-root/old.md"


def test_report_path_audit_normalizes_backslash_paths(tmp_path: Path):
    report_dir = tmp_path / "reports" / "predictions"
    report_dir.mkdir(parents=True)
    (report_dir / "current.md").write_text("current report", encoding="utf-8")
    archive_dir = tmp_path / "reports" / "archive" / "legacy-root"
    archive_dir.mkdir(parents=True)
    write_archive_manifest(repo_root=tmp_path)
    db_path = tmp_path / "local.db"
    _make_prediction_snapshot_db(db_path, "reports\\predictions\\current.md")

    result = apply_report_path_repairs(
        db_path=db_path,
        repo_root=tmp_path,
        backup_dir=tmp_path / "_archive" / "db_backups",
    )

    assert result["updated_count"] == 1
    conn = sqlite3.connect(db_path)
    try:
        stored = conn.execute("SELECT report_path FROM prediction_snapshots").fetchone()[0]
    finally:
        conn.close()
    assert stored == "reports/predictions/current.md"


def test_entrypoint_audit_flags_missing_current_and_stale_references(tmp_path: Path):
    (tmp_path / "backend" / "scripts").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".github").mkdir()
    for rel_path in [
        "backend/scripts/predict_match_full.py",
        "backend/scripts/run_postmatch_complete.py",
        "backend/scripts/run_accuracy_experiments.py",
        "backend/scripts/preflight_accuracy_experiments.py",
        "backend/scripts/audit_db_integrity.py",
        "backend/scripts/audit_public_outputs.py",
        "backend/scripts/collect_match_evidence.py",
        "backend/scripts/extract_information_signals.py",
        "backend/scripts/score_information_signals.py",
        "backend/scripts/audit_match_information_state.py",
    ]:
        (tmp_path / rel_path).write_text("# ok\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("Use daily_ops.py", encoding="utf-8")
    (tmp_path / "CONTRIBUTING.md").write_text("ok", encoding="utf-8")
    (tmp_path / "SECURITY.md").write_text("ok", encoding="utf-8")

    result = audit_entrypoints(repo_root=tmp_path)

    assert result["passed"] is False
    assert result["missing_current"] == []
    assert result["stale_references"][0]["entrypoint"] == "daily_ops.py"


def test_entrypoint_audit_passes_clean_current_entrypoints(tmp_path: Path):
    (tmp_path / "backend" / "scripts").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".github").mkdir()
    for rel_path in [
        "backend/scripts/predict_match_full.py",
        "backend/scripts/run_postmatch_complete.py",
        "backend/scripts/run_accuracy_experiments.py",
        "backend/scripts/preflight_accuracy_experiments.py",
        "backend/scripts/audit_db_integrity.py",
        "backend/scripts/audit_public_outputs.py",
        "backend/scripts/collect_match_evidence.py",
        "backend/scripts/extract_information_signals.py",
        "backend/scripts/score_information_signals.py",
        "backend/scripts/audit_match_information_state.py",
    ]:
        (tmp_path / rel_path).write_text("# ok\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("Use run_accuracy_experiments.py", encoding="utf-8")
    (tmp_path / "CONTRIBUTING.md").write_text("ok", encoding="utf-8")
    (tmp_path / "SECURITY.md").write_text("ok", encoding="utf-8")

    result = audit_entrypoints(repo_root=tmp_path)

    assert result["passed"] is True
    assert result["stale_references"] == []
