from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.audit_entrypoints import CURRENT_ENTRYPOINTS, audit_entrypoints
from scripts.audit_report_paths import (
    audit_report_paths,
    build_archive_manifest,
    write_archive_manifest,
    apply_report_path_repairs,
)
from scripts.audit_match_closed_loop import audit_match_closed_loop


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
    for rel_path in CURRENT_ENTRYPOINTS:
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
    for rel_path in CURRENT_ENTRYPOINTS:
        (tmp_path / rel_path).write_text("# ok\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("Use run_accuracy_experiments.py", encoding="utf-8")
    (tmp_path / "CONTRIBUTING.md").write_text("ok", encoding="utf-8")
    (tmp_path / "SECURITY.md").write_text("ok", encoding="utf-8")

    result = audit_entrypoints(repo_root=tmp_path)

    assert result["passed"] is True
    assert result["stale_references"] == []
    assert result["forbidden_runtime_references"] == []


def test_entrypoint_audit_flags_deleted_runtime_module_reference(tmp_path: Path):
    from scripts.audit_entrypoints import CURRENT_ENTRYPOINTS

    for rel_path in CURRENT_ENTRYPOINTS:
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# current\n", encoding="utf-8")
    dashboard = tmp_path / "backend" / "dashboard" / "page.py"
    dashboard.parent.mkdir(parents=True, exist_ok=True)
    dashboard.write_text(
        "from app.services.artifact_registry import load_registry\n",
        encoding="utf-8",
    )

    result = audit_entrypoints(repo_root=tmp_path)

    assert result["passed"] is False
    assert result["forbidden_runtime_references"][0]["path"] == (
        "backend/dashboard/page.py"
    )


def test_match_closed_loop_audit_flags_missing_pre_requirements(tmp_path: Path):
    db_path = tmp_path / "local.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE pre_match_snapshots (
                id TEXT PRIMARY KEY,
                match_id TEXT,
                snapshot_at TEXT,
                home_team TEXT,
                away_team TEXT,
                kickoff_at TEXT
            );
            CREATE TABLE prediction_snapshots (
                id TEXT PRIMARY KEY,
                match_id TEXT,
                generated_at TEXT,
                report_path TEXT
            );
            CREATE TABLE prediction_runs (id TEXT PRIMARY KEY, match_id TEXT);
            CREATE TABLE feature_snapshots (id TEXT PRIMARY KEY, match_id TEXT);
            CREATE TABLE evidence_items (id TEXT PRIMARY KEY, match_id TEXT);
            CREATE TABLE matches (id TEXT PRIMARY KEY);
            """
        )
        conn.execute(
            "INSERT INTO pre_match_snapshots VALUES (?, ?, ?, ?, ?, ?)",
            ("pre-1", "194", "2026-07-07T00:00:00+00:00", "Argentina", "Egypt", "2026-07-08T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    result = audit_match_closed_loop(db_path, match_ids=["194"], phase="pre", repo_root=tmp_path)

    assert result["passed"] is False
    assert "match_parent" in result["matches"][0]["missing"]
    assert "prediction_runs" in result["matches"][0]["missing"]
    assert "feature_snapshots" in result["matches"][0]["missing"]


def test_match_closed_loop_audit_passes_complete_pre_and_post(tmp_path: Path):
    report_dir = tmp_path / "reports" / "predictions"
    report_dir.mkdir(parents=True)
    (report_dir / "2026-07-07_Portugal_vs_Spain_prediction.md").write_text("# prediction", encoding="utf-8")
    post_dir = tmp_path / "reports" / "postmatch"
    post_dir.mkdir(parents=True)
    (post_dir / "2026-07-07_Portugal_Spain_postmatch.md").write_text("# post", encoding="utf-8")
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    (memory_dir / "wc-postmatch-Portugal-Spain-2026-07-07.md").write_text("# memory", encoding="utf-8")

    db_path = tmp_path / "local.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE pre_match_snapshots (
                id TEXT PRIMARY KEY,
                match_id TEXT,
                snapshot_at TEXT,
                home_team TEXT,
                away_team TEXT,
                kickoff_at TEXT
            );
            CREATE TABLE prediction_snapshots (
                id TEXT PRIMARY KEY,
                match_id TEXT,
                generated_at TEXT,
                report_path TEXT
            );
            CREATE TABLE prediction_runs (id TEXT PRIMARY KEY, match_id TEXT);
            CREATE TABLE feature_snapshots (id TEXT PRIMARY KEY, match_id TEXT);
            CREATE TABLE evidence_items (id TEXT PRIMARY KEY, match_id TEXT);
            CREATE TABLE match_results (id TEXT PRIMARY KEY, match_id TEXT);
            CREATE TABLE match_team_statistics (id TEXT PRIMARY KEY, match_id TEXT);
            CREATE TABLE postmatch_process_eval (id TEXT PRIMARY KEY, match_id TEXT);
            CREATE TABLE prediction_learning_log (id TEXT PRIMARY KEY, match_id TEXT);
            CREATE TABLE postmatch_eval (id TEXT PRIMARY KEY, prediction_run_id TEXT);
            CREATE TABLE signal_evaluations (id TEXT PRIMARY KEY, match_id TEXT);
            CREATE TABLE matches (id TEXT PRIMARY KEY);
            """
        )
        conn.execute(
            "INSERT INTO pre_match_snapshots VALUES (?, ?, ?, ?, ?, ?)",
            ("pre-1", "199", "2026-07-07T00:00:00+00:00", "Portugal", "Spain", "2026-07-07T20:00:00"),
        )
        conn.execute("INSERT INTO matches VALUES (?)", ("199",))
        conn.execute(
            "INSERT INTO prediction_snapshots VALUES (?, ?, ?, ?)",
            ("snap-1", "199", "2026-07-07T00:00:00+00:00", "reports/predictions/2026-07-07_Portugal_vs_Spain_prediction.md"),
        )
        conn.execute("INSERT INTO prediction_runs VALUES (?, ?)", ("run-1", "199"))
        conn.execute("INSERT INTO feature_snapshots VALUES (?, ?)", ("feat-1", "199"))
        conn.execute("INSERT INTO evidence_items VALUES (?, ?)", ("ev-1", "199"))
        conn.execute("INSERT INTO match_results VALUES (?, ?)", ("mr-1", "199"))
        conn.execute("INSERT INTO match_team_statistics VALUES (?, ?)", ("stat-1", "199"))
        conn.execute("INSERT INTO match_team_statistics VALUES (?, ?)", ("stat-2", "199"))
        conn.execute("INSERT INTO postmatch_process_eval VALUES (?, ?)", ("proc-1", "199"))
        conn.execute("INSERT INTO prediction_learning_log VALUES (?, ?)", ("log-1", "199"))
        conn.execute("INSERT INTO postmatch_eval VALUES (?, ?)", ("eval-1", "run-1"))
        conn.execute("INSERT INTO signal_evaluations VALUES (?, ?)", ("sig-eval-1", "199"))
        conn.commit()
    finally:
        conn.close()

    result = audit_match_closed_loop(db_path, match_ids=["199"], phase="all", repo_root=tmp_path)

    assert result["passed"] is True
    assert result["matches"][0]["missing"] == []
