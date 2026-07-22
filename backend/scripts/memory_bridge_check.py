#!/usr/bin/env python3
"""memory_bridge_check.py — Cross-reference memory files with DB records.

Part of Harness layer 3 (Context Management) and layer 6 (State/Session) —
ensures the 3 independent memory systems (33 memory files + project-status.md
+ DB) are not diverging silently.

Usage:
    python scripts/memory_bridge_check.py                  # check all
    python scripts/memory_bridge_check.py --match CIV-NOR  # check single

Exit codes:
    0 — all checks passed
    1 — warnings found (minor mismatches)
    2 — errors found (Brier diff > 0.05, missing records, etc.)
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Paths ───────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent  # backend/scripts/
BACKEND_DIR = SCRIPT_DIR.parent               # backend/
PROJECT_ROOT = BACKEND_DIR.parent             # project root
MEMORY_DIR = PROJECT_ROOT / "memory"
DB_PATH = BACKEND_DIR / "data" / "local_stage2.db"

# Known match name → (home_team, away_team) mapping from memory file naming
# convention: wc-postmatch-{HomeTeam}-{AwayTeam}-{date}.md
KNOWN_MEMORY_MATCHES = {
    "wc-postmatch-CzechRepublic-SouthAfrica-2026-06-27": ("Czech Republic", "South Africa"),
    "wc-postmatch-Mexico-SouthKorea-2026-06-11": ("Mexico", "South Korea"),
    "wc-postmatch-Argentina-Algeria-2026-06-11": ("Argentina", "Algeria"),
}

# Full mapping generated at runtime by scanning memory files


def parse_memory_file(path: Path) -> dict[str, Any] | None:
    """Extract Brier, direction, prediction probs, and DB refs from a memory file."""
    text = path.read_text(encoding="utf-8", errors="replace")

    result: dict[str, Any] = {
        "file": path.name,
        "brier": None,
        "direction": None,
        "prediction": None,
        "data_quality": None,
        "db_eval_id": None,
        "db_learning_log_id": None,
    }

    # Frontmatter DB refs
    frontmatter_match = re.search(r"---\n(.*?)\n---", text, re.DOTALL)
    if frontmatter_match:
        fm = frontmatter_match.group(1)
        for key in ("db_eval_id", "db_learning_log_id"):
            m = re.search(rf"{key}:\s*(\S+)", fm)
            if m:
                result[key] = m.group(1)

    # Body fields
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("- **Brier**:"):
            m = re.search(r"[\d.]+", line)
            if m:
                result["brier"] = float(m.group(0))
        elif line.startswith("- **Direction**:"):
            result["direction"] = "correct" if "correct" in line.lower() else (
                "wrong" if "wrong" in line.lower() else line.split(":**")[-1].strip()
            )
        elif line.startswith("- **Prediction**:"):
            result["prediction"] = line.split(":**")[-1].strip()
        elif line.startswith("- **Data quality**:"):
            result["data_quality"] = line.split(":**")[-1].strip()

    return result if any(v is not None for v in result.values() if v is not None) else None


def scan_memory_files() -> list[dict[str, Any]]:
    """Scan all memory files in memory/ directory."""
    if not MEMORY_DIR.exists():
        print(f"[WARN] Memory directory not found: {MEMORY_DIR}")
        return []

    results = []
    for f in sorted(MEMORY_DIR.glob("wc-postmatch-*.md")):
        parsed = parse_memory_file(f)
        if parsed:
            results.append(parsed)
    return results


def query_db_records() -> dict[str, Any]:
    """Query postmatch_eval and prediction_learning_log from SQLite DB."""
    if not DB_PATH.exists():
        print(f"[WARN] DB not found: {DB_PATH}")
        return {"evals": {}, "logs": {}, "eval_count": 0, "log_count": 0}

    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()

    # Get all postmatch evaluations with team names via JOIN
    try:
        c.execute("""
            SELECT pe.id, pe.brier_score, pe.actual_result,
                   t1.name as home_team, t2.name as away_team
            FROM postmatch_eval pe
            LEFT JOIN prediction_runs pr ON pe.prediction_run_id = pr.id
            LEFT JOIN matches m ON pr.match_id = m.id
            LEFT JOIN teams t1 ON m.home_team_id = t1.id
            LEFT JOIN teams t2 ON m.away_team_id = t2.id
            WHERE pe.brier_score IS NOT NULL
        """)
        evals = {}
        for row in c.fetchall():
            evals[row[0]] = {
                "brier": row[1],
                "result": row[2],
                "home_team": row[3] or "?",
                "away_team": row[4] or "?",
            }
    except Exception as e:
        print(f"[WARN] Could not query postmatch_eval with teams: {e}")
        # Fallback: query without JOIN
        c.execute("SELECT id, brier_score, actual_result FROM postmatch_eval WHERE brier_score IS NOT NULL")
        evals = {}
        for row in c.fetchall():
            evals[row[0]] = {"brier": row[1], "result": row[2], "home_team": "?", "away_team": "?"}

    # Get learning logs
    c.execute("SELECT id, match_id, error_magnitude FROM prediction_learning_log")
    logs = {}
    for row in c.fetchall():
        logs[row[0]] = {"match_id": row[1], "error_magnitude": row[2]}

    conn.close()
    return {
        "evals": evals,
        "logs": logs,
        "eval_count": len(evals),
        "log_count": len(logs),
    }


def build_memory_to_db_map(
    memory_records: list[dict[str, Any]],
    db_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Attempt to match memory files to DB records by team name patterns."""
    matches = []
    db_evals = db_data["evals"]

    for mem in memory_records:
        # Extract team names from filename
        fname = mem["file"]
        m = re.match(r"wc-postmatch-([^-]+)-([^-]+)-\d{4}-\d{2}-\d{2}\.md", fname)
        if not m:
            continue

        home_guess = m.group(1).replace("and", " & ")
        away_guess = m.group(2).replace("and", " & ")

        # Try to find matching DB record
        best_match = None
        for eval_id, edata in db_evals.items():
            h = (edata.get("home_team") or "").lower()
            a = (edata.get("away_team") or "").lower()
            if (home_guess.lower() in h or h in home_guess.lower()) and \
               (away_guess.lower() in a or a in away_guess.lower()):
                best_match = eval_id
                break

        matches.append({
            "memory_file": fname,
            "memory_brier": mem["brier"],
            "memory_direction": mem["direction"],
            "db_eval_id": best_match or mem.get("db_eval_id"),
            "db_brier": db_evals[best_match]["brier"] if best_match and best_match in db_evals else None,
            "has_db_ref": bool(mem.get("db_eval_id")),
            "home_guess": home_guess,
            "away_guess": away_guess,
        })

    return matches


def check_consistency(matched: list[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    """Check for inconsistencies between memory and DB."""
    errors: list[str] = []
    warnings: list[str] = []
    info: list[str] = []

    for m in matched:
        fname = m["memory_file"]

        # Check 1: DB reference present?
        if not m["db_eval_id"]:
            warnings.append(
                f"[{fname}] No DB reference (db_eval_id). Add frontmatter field "
                f"to enable cross-system traceability."
            )

        # Check 2: Brier matches (if both exist)?
        mem_brier = m["memory_brier"]
        db_brier = m["db_brier"]
        if mem_brier is not None and db_brier is not None:
            diff = abs(mem_brier - db_brier)
            if diff > 0.05:
                errors.append(
                    f"[{fname}] Brier mismatch: memory={mem_brier:.4f} vs "
                    f"DB={db_brier:.4f} (diff={diff:.4f}). "
                    f"One system has stale data."
                )
            elif diff > 0.01:
                warnings.append(
                    f"[{fname}] Brier slight diff: memory={mem_brier:.4f} vs "
                    f"DB={db_brier:.4f} (diff={diff:.4f})."
                )
            else:
                info.append(f"[{fname}] Brier consistent: {mem_brier:.4f} ✓")
        elif mem_brier is not None and db_brier is None:
            warnings.append(
                f"[{fname}] Memory has Brier={mem_brier:.4f} but no DB record found. "
                f"Post-match eval may not have been written to DB."
            )
        elif mem_brier is None and db_brier is not None:
            warnings.append(
                f"[{fname}] DB has Brier={db_brier:.4f} but memory file is missing "
                f"the Brier value. Update memory file."
            )

    return errors, warnings, info


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-check memory files against DB records"
    )
    parser.add_argument("--match", help="Filter to a specific match (e.g. CIV-NOR)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all checks (not just issues)")
    args = parser.parse_args()

    print("=" * 60)
    print("Memory Bridge — Consistency Check")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Memory dir: {MEMORY_DIR}")
    print(f"  DB: {DB_PATH}")
    print("=" * 60)
    print()

    # Step 1: Scan memory files
    memory_records = scan_memory_files()
    print(f"[SCAN] Found {len(memory_records)} memory files")

    if args.match:
        memory_records = [r for r in memory_records if args.match.lower() in r["file"].lower()]
        if not memory_records:
            print(f"[WARN] No memory files match filter: {args.match}")
            return 0
        print(f"[FILTER] {len(memory_records)} match '{args.match}'")

    # Step 2: Query DB
    db_data = query_db_records()
    print(f"[DB]   {db_data['eval_count']} postmatch evaluations")
    print(f"[DB]   {db_data['log_count']} learning logs")
    print()

    # Step 3: Match & check
    matched = build_memory_to_db_map(memory_records, db_data)

    if not matched:
        print("[INFO] No memory files with cross-referenceable data found.")
        print("       This is expected if memory files haven't been updated with db_refs yet.")
        return 0

    errors, warnings, info = check_consistency(matched)

    # Step 4: Report
    print("── Results ──")
    print(f"  Files checked: {len(matched)}")
    print(f"  With DB refs:  {sum(1 for m in matched if m['has_db_ref'])}")
    print(f"  With DB match: {sum(1 for m in matched if m['db_eval_id'])}")
    print()

    if errors:
        print(f"[ERROR] {len(errors)} critical mismatch(es):")
        for e in errors:
            print(f"  {e}")
        print()

    if warnings:
        print(f"[WARN]  {len(warnings)} warning(s):")
        for w in warnings:
            print(f"  {w}")
        print()

    if args.verbose and info:
        print(f"[INFO]  {len(info)} OK check(s):")
        for i in info:
            print(f"  {i}")
        print()

    if not errors and not warnings:
        print("[OK] All checks passed. Memory files and DB are consistent.")

    # Summary
    print("── Summary ──")
    print(f"  DB references coverage: "
          f"{sum(1 for m in matched if m['has_db_ref'])}/{len(matched)}")
    if len(matched) > 0 and sum(1 for m in matched if m['has_db_ref']) == 0:
        print("  NOTE: No memory files have db_refs yet.")
        print("  To add: edit memory file frontmatter, add:")
        print('    db_eval_id: <UUID from postmatch_eval>')
        print('    db_learning_log_id: <UUID from prediction_learning_log>')
        print("  Run backend/scripts/list_postmatch_ids.py to find IDs.")

    if errors:
        return 2
    if warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
