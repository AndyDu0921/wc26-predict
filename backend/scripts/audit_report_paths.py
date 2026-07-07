#!/usr/bin/env python3
"""Audit report traceability across DB paths and archived report files."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_DB_PATH = BACKEND_DIR / "data" / "local_stage2.db"
DEFAULT_ARCHIVE_REL = Path("reports/archive/legacy-root")
DEFAULT_BACKUP_DIR = REPO_ROOT / "_archive" / "db_backups" / "20260706"
MANIFEST_NAME = "manifest.json"


def build_archive_manifest(
    *,
    repo_root: Path = REPO_ROOT,
    archive_rel: Path = DEFAULT_ARCHIVE_REL,
) -> dict[str, Any]:
    archive_dir = repo_root / archive_rel
    files: list[dict[str, Any]] = []
    if archive_dir.exists():
        for path in sorted(archive_dir.rglob("*")):
            if not path.is_file() or path.name in {MANIFEST_NAME, "README.md"}:
                continue
            files.append(
                {
                    "path": _repo_rel(path, repo_root),
                    "name": path.name,
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    return {
        "schema_version": "report_archive_manifest.v1",
        "archive_dir": archive_rel.as_posix(),
        "file_count": len(files),
        "files": files,
    }


def write_archive_manifest(
    *,
    repo_root: Path = REPO_ROOT,
    archive_rel: Path = DEFAULT_ARCHIVE_REL,
) -> Path:
    archive_dir = repo_root / archive_rel
    archive_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = archive_dir / MANIFEST_NAME
    manifest = build_archive_manifest(repo_root=repo_root, archive_rel=archive_rel)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def audit_report_paths(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    repo_root: Path = REPO_ROOT,
    archive_rel: Path = DEFAULT_ARCHIVE_REL,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    archive_dir = repo_root / archive_rel
    manifest_path = manifest_path or archive_dir / MANIFEST_NAME
    manifest = _load_manifest(manifest_path)
    manifest_findings = _audit_manifest(manifest, repo_root=repo_root)
    db_findings = _audit_db_paths(db_path=db_path, repo_root=repo_root, archive_rel=archive_rel)
    report_root_files = sorted(
        _repo_rel(path, repo_root)
        for path in (repo_root / "reports").glob("*.md")
    ) if (repo_root / "reports").exists() else []

    summary = {
        "db_report_paths": db_findings["summary"],
        "manifest": {
            "exists": manifest_path.exists(),
            "file_count": len(manifest.get("files", [])) if manifest else 0,
            "finding_count": len(manifest_findings),
        },
        "root_report_file_count": len(report_root_files),
    }
    passed = (
        summary["db_report_paths"]["missing_count"] == 0
        and summary["db_report_paths"]["legacy_root_count"] == 0
        and summary["manifest"]["exists"]
        and summary["manifest"]["finding_count"] == 0
        and summary["root_report_file_count"] == 0
    )
    return {
        "schema_version": "report_path_audit.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "archive_dir": archive_rel.as_posix(),
        "passed": passed,
        "summary": summary,
        "db_findings": db_findings["findings"],
        "manifest_findings": manifest_findings,
        "root_report_files": report_root_files,
    }


def apply_report_path_repairs(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    repo_root: Path = REPO_ROOT,
    archive_rel: Path = DEFAULT_ARCHIVE_REL,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> dict[str, Any]:
    before = audit_report_paths(db_path=db_path, repo_root=repo_root, archive_rel=archive_rel)
    repairable = [
        item
        for item in before["db_findings"]
        if item["status"] in {"archive_hit", "separator_normalizable"}
    ]
    backup_path = None
    if repairable:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_dir / f"{db_path.stem}.report-path-audit-backup-{stamp}{db_path.suffix}"
        shutil.copy2(db_path, backup_path)
        conn = sqlite3.connect(db_path)
        try:
            for item in repairable:
                conn.execute(
                    "UPDATE prediction_snapshots SET report_path = ? WHERE id = ?",
                    (item["recommended_path"], item["id"]),
                )
            conn.commit()
        finally:
            conn.close()
    after = audit_report_paths(db_path=db_path, repo_root=repo_root, archive_rel=archive_rel)
    return {
        "schema_version": "report_path_repair.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "backup_path": str(backup_path) if backup_path else None,
        "updated_count": len(repairable),
        "updated_ids": [item["id"] for item in repairable],
        "before_summary": before["summary"],
        "after_summary": after["summary"],
        "passed_after": after["passed"],
    }


def _audit_db_paths(*, db_path: Path, repo_root: Path, archive_rel: Path) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    report_markdown_rows = 0
    if not db_path.exists():
        return {
            "summary": {
                "checked_count": 0,
                "ok_count": 0,
                "missing_count": 1,
                "legacy_root_count": 0,
                "archive_hit_count": 0,
                "separator_normalizable_count": 0,
                "report_markdown_rows": 0,
            },
            "findings": [{"status": "missing_db", "path": str(db_path)}],
        }

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if _has_table(conn, "pre_match_snapshots") and "report_markdown" in _columns(conn, "pre_match_snapshots"):
            report_markdown_rows = int(
                conn.execute(
                    "SELECT COUNT(*) FROM pre_match_snapshots WHERE report_markdown IS NOT NULL AND report_markdown <> ''"
                ).fetchone()[0]
            )
        if _has_table(conn, "prediction_snapshots") and "report_path" in _columns(conn, "prediction_snapshots"):
            rows = conn.execute(
                """
                SELECT id, match_id, report_path
                FROM prediction_snapshots
                WHERE report_path IS NOT NULL AND report_path <> ''
                ORDER BY id
                """
            ).fetchall()
        else:
            rows = []
    finally:
        conn.close()

    for row in rows:
        raw_path = str(row["report_path"])
        normalized = raw_path.replace("\\", "/")
        current_path = repo_root / normalized
        archive_path = repo_root / archive_rel / Path(normalized).name
        is_legacy_root = normalized.startswith("reports/") and "/" not in normalized[len("reports/"):]
        if current_path.exists() and not is_legacy_root:
            status = "separator_normalizable" if raw_path != normalized else "ok"
            recommended_path = normalized
        elif archive_path.exists():
            status = "archive_hit"
            recommended_path = (archive_rel / Path(normalized).name).as_posix()
        elif is_legacy_root:
            status = "legacy_root_missing"
            recommended_path = None
        else:
            status = "missing"
            recommended_path = None
        findings.append(
            {
                "table": "prediction_snapshots",
                "id": str(row["id"]),
                "match_id": str(row["match_id"]),
                "raw_path": raw_path,
                "normalized_path": normalized,
                "status": status,
                "recommended_path": recommended_path,
            }
        )

    summary = {
        "checked_count": len(findings),
        "ok_count": sum(1 for item in findings if item["status"] == "ok"),
        "missing_count": sum(1 for item in findings if item["status"] in {"missing", "legacy_root_missing"}),
        "legacy_root_count": sum(1 for item in findings if item["status"] in {"archive_hit", "legacy_root_missing"}),
        "archive_hit_count": sum(1 for item in findings if item["status"] == "archive_hit"),
        "separator_normalizable_count": sum(1 for item in findings if item["status"] == "separator_normalizable"),
        "report_markdown_rows": report_markdown_rows,
    }
    return {"summary": summary, "findings": findings}


def _audit_manifest(manifest: dict[str, Any], *, repo_root: Path) -> list[dict[str, Any]]:
    if not manifest:
        return [{"status": "missing_manifest"}]
    findings = []
    for item in manifest.get("files", []):
        path = repo_root / item["path"]
        if not path.exists():
            findings.append({"status": "missing_archive_file", "path": item["path"]})
            continue
        size = path.stat().st_size
        if int(item.get("size", -1)) != size:
            findings.append({"status": "size_mismatch", "path": item["path"]})
        if item.get("sha256") != _sha256(path):
            findings.append({"status": "sha256_mismatch", "path": item["path"]})
    return findings


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"files": [], "_error": "invalid_json"}


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _repo_rel(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit DB report paths and archived report manifest")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--archive-rel", default=DEFAULT_ARCHIVE_REL.as_posix())
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Repair archive hits and path separators in prediction_snapshots")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    db_path = Path(args.db_path)
    archive_rel = Path(args.archive_rel)
    if args.write_manifest:
        write_archive_manifest(archive_rel=archive_rel)
    if args.apply:
        payload = apply_report_path_repairs(db_path=db_path, archive_rel=archive_rel)
        passed = bool(payload["passed_after"])
    else:
        payload = audit_report_paths(db_path=db_path, archive_rel=archive_rel)
        passed = bool(payload["passed"])
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        summary = payload.get("after_summary") or payload.get("summary")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
