#!/usr/bin/env python3
"""Auto post-match learning — daily runner.

Finds matches that finished yesterday, matches them to prediction snapshots,
and runs the LearningEngine to attribute errors and update signal tracking.

V4.6-process-eval: Now reads learning_weight from postmatch_process_eval
to gate learning intensity — low-signal matches don't drive parameter changes.

Usage:
    python scripts/auto_postmatch.py           # Yesterday's matches
    python scripts/auto_postmatch.py --days 3  # Last 3 days
    python scripts/auto_postmatch.py --dry-run # Show what would be processed
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from uuid import UUID

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.match import Match, MatchResult
from app.models.prediction_snapshot import PredictionSnapshot
from app.models.prediction_learning_log import PredictionLearningLog
from app.services.learning_engine import get_learning_engine
from app.services.result_verification import (
    get_verification_service,
    SourceTier,
)


def _lookup_process_eval_weight(
    home_team: str, away_team: str,
) -> tuple[float, str, str | None, str | None]:
    """Look up the process evaluation learning_weight for a match by team names.

    Queries the SQLite postmatch_process_eval table (linked via wc26_schedule by
    team names) and returns the learning_weight, learning_tier, failure_type,
    and process_label from the process evaluator.

    Returns:
        (learning_weight, tier, model_failure_type, process_label)
        Default: (1.0, "full", None, None) if no evaluation exists yet.
    """
    db_path = BACKEND_DIR / "data" / "local_stage2.db"
    if not db_path.exists():
        return (1.0, "full", None, None)

    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        # Join postmatch_process_eval → wc26_schedule on match_id to match by team names
        cur.execute(
            "SELECT ppe.learning_weight, ppe.model_failure_type, ppe.process_label "
            "FROM postmatch_process_eval ppe "
            "JOIN wc26_schedule s ON ppe.match_id = s.id "
            "WHERE s.home_team = ? AND s.away_team = ? "
            "ORDER BY ppe.id DESC LIMIT 1",
            (home_team, away_team),
        )
        row = cur.fetchone()
        conn.close()

        if row is None:
            return (1.0, "full", None, None)

        weight = float(row[0]) if row[0] is not None else 1.0
        if weight >= 0.70:
            tier = "full"
        elif weight >= 0.30:
            tier = "diagnostic"
        else:
            tier = "record_only"

        return (weight, tier, str(row[1]) if row[1] else None, str(row[2]) if row[2] else None)
    except Exception:
        return (1.0, "full", None, None)


async def auto_postmatch(days: int = 1, dry_run: bool = False) -> dict:
    """Process post-match learning for finished matches.

    Returns summary dict with counts.
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    async with AsyncSessionLocal() as db:
        # Find matches finished in the window (use raw SQL to bypass UUID ORM issue)
        from sqlalchemy import text
        stmt = text("""
            SELECT m.id as m_id, mr.home_goals, mr.away_goals,
                   m.match_date, m.home_team_id, m.away_team_id,
                   ht.name as home_name, at.name as away_name
            FROM matches m
            JOIN match_results mr ON mr.match_id = m.id
            JOIN teams ht ON m.home_team_id = ht.id
            JOIN teams at ON m.away_team_id = at.id
            WHERE m.match_date >= :since AND m.match_date < :now AND m.status = 'finished'
            ORDER BY m.match_date DESC
        """)
        result = await db.execute(stmt, {"since": since, "now": now})
        finished_matches = result.fetchall()

        processed = 0
        skipped_no_snapshot = 0
        skipped_already_logged = 0
        skipped_insufficient_sources = 0
        skipped_verification_failed = 0
        total_brier = 0.0
        engine = get_learning_engine()

        for row in finished_matches:
            home_goals = row.home_goals
            away_goals = row.away_goals
            if home_goals is None or away_goals is None:
                continue

            # Find prediction snapshots — convert CHAR(32) match_id to
            # UUID format (36-char with hyphens) for LIKE match against
            # PredictionSnapshot.match_id which stores hyphenated UUIDs.
            match_id_raw = row.m_id
            if len(match_id_raw) == 32:
                match_id_fmt = f"{match_id_raw[:8]}-{match_id_raw[8:12]}-{match_id_raw[12:16]}-{match_id_raw[16:20]}-{match_id_raw[20:]}"
            else:
                match_id_fmt = match_id_raw
            snap_stmt = (
                select(PredictionSnapshot)
                .where(PredictionSnapshot.match_id.like(f"{match_id_fmt}%"))
                .order_by(PredictionSnapshot.generated_at.desc())
                .limit(1)
            )
            snap_result = await db.execute(snap_stmt)
            snapshot = snap_result.scalar_one_or_none()

            if snapshot is None:
                skipped_no_snapshot += 1
                continue

            # Check if already processed
            existing = await db.execute(
                select(PredictionLearningLog).where(
                    PredictionLearningLog.snapshot_id == snapshot.id
                )
            )
            if existing.scalar_one_or_none() is not None:
                skipped_already_logged += 1
                continue

            # ── Verification gate ─────────────────────────────────
            # HARD RULE: require ≥2 independent source claims before learning.
            # A single source (match_results table alone) is NOT sufficient —
            # this prevents wrong scores from silently entering the learning log.
            # Use run_postmatch.py for manual verification with a second source.
            verification_service = get_verification_service()
            match_uuid = UUID(match_id_raw)
            verified_result_id: str | None = None

            # Source 1: match_results table (tier 3)
            try:
                await verification_service.add_source_result(
                    db=db,
                    match_id=match_uuid,
                    home_goals=int(home_goals),
                    away_goals=int(away_goals),
                    source_name="match_results_import",
                    source_tier=SourceTier.REPUTABLE_DATA_PROVIDER,
                    match_status="Finished",
                    notes=f"Auto-recorded from match_results table via auto_postmatch (snapshot={snapshot.id})",
                )
            except ValueError:
                # Match status not acceptable — skip learning entirely
                skipped_verification_failed += 1
                print(f"  ⚠ {row.match_date[:10]} {snapshot.home_team} vs "
                      f"{snapshot.away_team}: match status rejected by verification gate")
                continue

            # Source 2: auto_postmatch batch verification (tier 4)
            # Audit R4-C10: auto_postmatch had only 1 source → consensus
            # never reached is_verified=True.  Add a second automatic source
            # at REPUTABLE_MEDIA tier so automated batch learning works.
            try:
                await verification_service.add_source_result(
                    db=db,
                    match_id=match_uuid,
                    home_goals=int(home_goals),
                    away_goals=int(away_goals),
                    source_name="auto_postmatch_batch",
                    source_tier=SourceTier.REPUTABLE_MEDIA,
                    match_status="Finished",
                    notes="Automated batch post-match run",
                )
            except ValueError:
                pass  # Duplicate or status mismatch — non-fatal

            # Build consensus from all available source claims
            consensus = await verification_service.build_consensus(db, match_uuid)

            if consensus is None or not consensus.is_verified:
                skipped_insufficient_sources += 1
                source_list = consensus.source_names if consensus else ["none"]
                print(f"  ⛔ SKIPPED: {row.match_date[:10]} {snapshot.home_team} vs "
                      f"{snapshot.away_team} — insufficient sources "
                      f"({consensus.source_count if consensus else 0}/2 required: "
                      f"{', '.join(source_list)})")
                print(f"     → Use run_postmatch.py --match-id {match_id_raw} "
                      f"--home-score {home_goals} --away-score {away_goals} "
                      f"--verify-url <URL> for manual verification")
                continue

            # Consensus achieved — proceed with verified learning
            verified_result_id = str(consensus.verification_id)
            print(f"  🔒 Verified: {consensus.home_goals}-{consensus.away_goals} "
                  f"({consensus.source_count} sources: {', '.join(consensus.source_names)})")
            # ── End verification gate ──────────────────────────────

            # ── Process-eval learning weight (V4.6) ─────────────────
            learning_weight, learning_tier, failure_type, process_label = (
                _lookup_process_eval_weight(
                    getattr(snapshot, 'home_team', ''),
                    getattr(snapshot, 'away_team', ''),
                )
            )

            if dry_run:
                print(f"[DRY-RUN] {row.match_date[:10]} {snapshot.home_team} "
                      f"{home_goals}-{away_goals} {snapshot.away_team} "
                      f"lw={learning_weight:.2f}/{learning_tier}")
                processed += 1
                continue

            # Run learning
            try:
                error_log = await engine.process_match_result(
                    snapshot,
                    int(home_goals),
                    int(away_goals),
                    db,
                    verified_result_id=verified_result_id,
                    learning_weight=learning_weight,
                )
                total_brier += error_log.error_magnitude
                processed += 1
                print(f"  ✓ {row.match_date[:10]} {snapshot.home_team} "
                      f"{home_goals}-{away_goals} {snapshot.away_team} "
                      f"Brier={error_log.error_magnitude:.3f} "
                      f"dir={error_log.error_direction} "
                      f"lw={learning_weight:.2f}/{learning_tier} "
                      f"status={error_log.status}")
            except Exception as e:
                await db.rollback()
                # Avoid accessing lazy attributes after rollback — use pre-extracted values
                h_name = getattr(snapshot, 'home_team', '?')
                a_name = getattr(snapshot, 'away_team', '?') 
                print(f"  ✗ {h_name} vs {a_name}: {e}")

    avg_brier = total_brier / processed if processed else 0
    summary = {
        "processed": processed,
        "skipped_no_snapshot": skipped_no_snapshot,
        "skipped_already_logged": skipped_already_logged,
        "skipped_insufficient_sources": skipped_insufficient_sources,
        "skipped_verification_failed": skipped_verification_failed,
        "avg_brier": avg_brier,
    }
    return summary


def main():
    parser = argparse.ArgumentParser(description="Auto post-match learning")
    parser.add_argument("--days", type=int, default=1, help="Days back to look (default: 1)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed")
    args = parser.parse_args()

    summary = asyncio.run(auto_postmatch(days=args.days, dry_run=args.dry_run))

    print(f"\n---")
    print(f"Processed: {summary['processed']}")
    print(f"Skipped (no snapshot): {summary['skipped_no_snapshot']}")
    print(f"Skipped (already logged): {summary['skipped_already_logged']}")
    print(f"Skipped (insufficient sources): {summary['skipped_insufficient_sources']}")
    print(f"Skipped (verification failed): {summary['skipped_verification_failed']}")
    if summary['processed']:
        print(f"Average Brier: {summary['avg_brier']:.3f}")


if __name__ == "__main__":
    main()
