"""Shared public-output audit helpers.

Market odds and bookmaker consensus are allowed as research evidence. The audit
only blocks advice-like or guaranteed-outcome language.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from app.services.public_safety_filter import (
    CREATOR_SAFE_FORBIDDEN,
    PUBLIC_SAFE_EXTRA_FORBIDDEN,
    scan_text,
)


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".html",
    ".htm",
    ".json",
    ".csv",
}
DEFAULT_PATHS = (REPO_ROOT / "reports",)
ARCHIVE_DIR_NAMES = {"archive"}


def iter_public_files(paths: Iterable[Path], *, include_archive: bool = False) -> Iterable[Path]:
    """Yield public text files under the provided paths."""
    for path in paths:
        resolved = path.resolve()
        if not resolved.exists():
            continue
        if resolved.is_file():
            if resolved.suffix.lower() in TEXT_SUFFIXES:
                yield resolved
            continue
        for child in resolved.rglob("*"):
            if not include_archive and _under_archive(child, resolved):
                continue
            if child.is_file() and child.suffix.lower() in TEXT_SUFFIXES:
                yield child


def audit_paths(paths: Iterable[Path], *, mode: str = "creator_safe", include_archive: bool = False) -> dict:
    """Scan public files and return a structured audit result."""
    terms = list(CREATOR_SAFE_FORBIDDEN)
    if mode == "public_safe":
        terms.extend(PUBLIC_SAFE_EXTRA_FORBIDDEN)

    files = list(iter_public_files(paths, include_archive=include_archive))
    findings = []
    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            findings.append(
                {
                    "path": str(file_path),
                    "term": "read_error",
                    "line": 0,
                    "context": str(exc),
                }
            )
            continue
        for finding in scan_text(text, terms):
            findings.append(
                {
                    "path": str(file_path),
                    "term": finding["term"],
                    "line": finding["line"],
                    "context": finding["context"],
                }
            )

    return {
        "passed": not findings,
        "mode": mode,
        "include_archive": include_archive,
        "files_scanned": len(files),
        "findings": findings,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan public outputs for unsafe betting-advice language",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files or directories to scan. Defaults to reports/.",
    )
    parser.add_argument(
        "--mode",
        choices=("creator_safe", "public_safe"),
        default="creator_safe",
    )
    parser.add_argument(
        "--include-archive",
        action="store_true",
        help="Also scan reports/archive historical files.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = [Path(item) for item in args.paths] if args.paths else list(DEFAULT_PATHS)
    result = audit_paths(paths, mode=args.mode, include_archive=args.include_archive)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["passed"]:
        print(
            f"PASS: scanned {result['files_scanned']} public files; "
            "no unsafe betting-advice terms found."
        )
    else:
        print(
            f"FAIL: {len(result['findings'])} findings in "
            f"{result['files_scanned']} public files."
        )
        for finding in result["findings"][:50]:
            print(
                f"{finding['path']}:{finding['line']} "
                f"[{finding['term']}] {finding['context']}"
            )
        if len(result["findings"]) > 50:
            print(f"... {len(result['findings']) - 50} more findings")

    return 0 if result["passed"] else 1


def _under_archive(path: Path, root: Path) -> bool:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return any(part.lower() in ARCHIVE_DIR_NAMES for part in relative.parts[:-1])
