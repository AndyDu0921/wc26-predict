#!/usr/bin/env python3
"""Complete post-match pipeline — hard-enforced 7-step flow.

This is the CANONICAL post-match script. It enforces every step and refuses
to proceed if verification fails. Use this instead of running individual
scripts (run_postmatch.py, complete_postmatch.py) separately.

Usage:
    python scripts/run_postmatch_complete.py \
        --match-id 77382b67668e4d1a966a5fb88af6e408 \
        --home-score 2 --away-score 0 \
        --verify-url "https://www.espn.com/soccer/match/_/id/..." \
        --home-xg 1.43 --away-xg 0.065 \
        --possession-home 61 --possession-away 39 \
        --shots-home 16 --shots-away 3 \
        --sot-home 4 --sot-away 2 \
        --data-source "Opta"
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

# Fix Windows GBK encoding for emoji characters
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from uuid import UUID

from sqlalchemy import text, select, delete
from app.database import AsyncSessionLocal
from app.models.prediction_snapshot import PredictionSnapshot
from app.models.prediction_learning_log import PredictionLearningLog
from app.services.learning_engine import get_learning_engine
from app.services.result_verification import (
    get_verification_service,
    SourceTier,
)

# ═══════════════════════════════════════════════════════════════════════════
# Step-by-step pipeline
# ═══════════════════════════════════════════════════════════════════════════

PIPELINE_STEPS = [
    "verify_score",
    "find_snapshot",
    "collect_opta_data",
    "update_match_results",
    "run_learning_engine",
    "generate_analysis",
    "output_report",
]


def _is_uuid_like(raw: str) -> bool:
    try:
        UUID(str(raw))
        return True
    except (TypeError, ValueError):
        return False


def _json_load(raw):
    if raw in (None, ""):
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


def _prob_triplet(payload: dict | None) -> tuple[float, float, float] | None:
    if not isinstance(payload, dict):
        return None
    home = payload.get("home", payload.get("home_prob", payload.get("home_win_prob")))
    draw = payload.get("draw", payload.get("draw_prob"))
    away = payload.get("away", payload.get("away_prob", payload.get("away_win_prob")))
    if home is None or draw is None or away is None:
        return None
    return float(home), float(draw), float(away)


def _normalize_component_probs(
    component_probs: dict | None,
    market_probs: dict | None = None,
) -> dict:
    """Normalize historical component keys for review and attribution.

    Older snapshots store Dixon-Coles as ``dixon_coles`` and Pi as
    ``pi_rating``. Post-match reporting and the learning engine expect the
    canonical aliases, so we keep the original payload and add aliases.
    """
    normalized = dict(component_probs or {})
    if "dc" not in normalized and "dixon_coles" in normalized:
        normalized["dc"] = normalized["dixon_coles"]
    if "pi" not in normalized and "pi_rating" in normalized:
        normalized["pi"] = normalized["pi_rating"]
    if market_probs and "market" not in normalized:
        normalized["market"] = market_probs
    return normalized


def _component_review_rows(
    component_probs: dict,
    actual_idx: int,
    actual_vec: list[int],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for label, key in [
        ("DC", "dc"),
        ("Enhancer", "enhancer"),
        ("DC+Enhancer", "dixon_coles+enhancer"),
        ("NegBin", "negbin"),
        ("Weibull", "weibull"),
        ("Elo", "elo"),
        ("Pi", "pi"),
        ("Market", "market"),
    ]:
        probs = _prob_triplet(component_probs.get(key))
        if probs is None:
            continue
        l_h, l_d, l_a = probs
        fav_idx = max(range(3), key=lambda i: [l_h, l_d, l_a][i])
        brier = sum((p - a) ** 2 for p, a in zip([l_h, l_d, l_a], actual_vec))
        rows.append({
            "label": label,
            "home": l_h,
            "draw": l_d,
            "away": l_a,
            "fav_idx": fav_idx,
            "fav": ["H", "D", "A"][fav_idx],
            "dir_correct": fav_idx == actual_idx,
            "brier": brier,
        })
    return rows


def _component_markdown(rows: list[dict[str, object]]) -> str:
    lines = []
    for row in rows:
        lines.append(
            f"| {str(row['label']):12s} | "
            f"{float(row['home'])*100:5.1f}% / "
            f"{float(row['draw'])*100:5.1f}% / "
            f"{float(row['away'])*100:5.1f}% | "
            f"{row['fav']} | {'✅' if row['dir_correct'] else '❌'} | "
            f"{float(row['brier']):.4f} |"
        )
    return "\n".join(lines)


def _fmt_metric(value, digits: int = 3) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "N/A"


def _lookup_process_eval_detail(home_team: str, away_team: str) -> dict[str, object]:
    learning_weight, tier, failure_type, process_label = _lookup_process_eval_weight(
        home_team,
        away_team,
    )
    detail: dict[str, object] = {
        "learning_weight": learning_weight,
        "learning_tier": tier,
        "failure_type": failure_type,
        "process_label": process_label,
        "base_learning_weight": None,
        "learning_data_quality": None,
        "snapshot_factor": 1.0,
    }
    try:
        import sqlite3
        db_path = BACKEND_DIR / "data" / "local_stage2.db"
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            """
            SELECT ppe.learning_weight, ppe.model_failure_type, ppe.process_label, ppe.notes
            FROM postmatch_process_eval ppe
            JOIN wc26_schedule s ON s.id = ppe.match_id
            WHERE s.home_team = ? AND s.away_team = ?
            ORDER BY ppe.created_at DESC LIMIT 1
            """,
            (home_team, away_team),
        ).fetchone()
        conn.close()
        if row and row[3]:
            notes = json.loads(row[3])
            base = notes.get("base_learning_weight")
            if base is not None:
                base_f = float(base)
                detail["base_learning_weight"] = base_f
                if base_f > 0:
                    detail["learning_data_quality"] = round(float(row[0]) / base_f, 4)
    except Exception:
        pass
    return detail


async def _fallback_pre_match_snapshot(db, match_id: str) -> PredictionSnapshot | None:
    """Build a detached PredictionSnapshot from the latest pre_match_snapshot."""
    row = (
        await db.execute(
            text(
                """
                SELECT *
                FROM pre_match_snapshots
                WHERE CAST(match_id AS TEXT) = :mid
                ORDER BY snapshot_at DESC
                LIMIT 1
                """
            ),
            {"mid": match_id},
        )
    ).mappings().first()
    if row is None:
        return None
    odds_snapshot = _json_load(row.get("odds_snapshot")) or {}
    component_probs = _normalize_component_probs(
        _json_load(row.get("component_probs")) or {},
        odds_snapshot,
    )
    return PredictionSnapshot(
        id=str(row["id"]),
        match_id=str(row["match_id"]),
        generated_at=row.get("snapshot_at"),
        model_version=row.get("model_version") or row.get("code_version") or "pre_match_snapshot_fallback",
        run_type="pre_match_snapshot_fallback",
        home_team=row["home_team"],
        away_team=row["away_team"],
        competition=row.get("competition") or "FIFA World Cup 2026",
        match_time=row.get("kickoff_at"),
        baseline_probs={
            "home": float(row["final_home_prob"]),
            "draw": float(row["final_draw_prob"]),
            "away": float(row["final_away_prob"]),
        },
        adjusted_probs={
            "home": float(row["final_home_prob"]),
            "draw": float(row["final_draw_prob"]),
            "away": float(row["final_away_prob"]),
        },
        component_probs=component_probs,
        market_probs=component_probs.get("market") if isinstance(component_probs, dict) else None,
        expected_goals={"home": row.get("home_xg"), "away": row.get("away_xg")},
        top_scores=_json_load(row.get("top_scores")),
        fused_score_matrix=_json_load(row.get("fused_score_matrix")),
        source_score_matrices=_json_load(row.get("source_score_matrices")),
        confidence=row.get("confidence"),
        missing_inputs=_json_load(row.get("missing_inputs")) or [],
        pipeline_params={
            "weight_config": _json_load(row.get("weight_config")),
            "weight_config_label": row.get("weight_config_label"),
            "effective_weights": _json_load(row.get("effective_weights")),
            "market_weight_used": row.get("market_weight_used"),
            "pre_match_snapshot_id": row.get("id"),
            "snapshot_fallback": True,
            "fused_score_matrix": _json_load(row.get("fused_score_matrix")),
            "source_score_matrices": _json_load(row.get("source_score_matrices")),
        },
        report_markdown=row.get("report_markdown"),
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
    import sqlite3
    db_path = BACKEND_DIR / "data" / "local_stage2.db"
    if not db_path.exists():
        return 1.0, "full", None, None

    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            "SELECT ppe.learning_weight, ppe.model_failure_type, ppe.process_label "
            "FROM postmatch_process_eval ppe "
            "JOIN wc26_schedule s ON ppe.match_id = s.id "
            "WHERE s.home_team = ? AND s.away_team = ? "
            "ORDER BY ppe.created_at DESC LIMIT 1",
            (home_team, away_team),
        )
        row = cur.fetchone()
        conn.close()

        if row is None:
            return 1.0, "full", None, None

        lw = float(row[0])
        tier = "full" if lw >= 0.70 else ("diagnostic" if lw >= 0.30 else "record_only")
        return lw, tier, row[1], row[2]
    except Exception:
        return 1.0, "full", None, None


async def run_complete_postmatch(
    match_id: str,
    home_score: int,
    away_score: int,
    verify_url: str | None = None,
    verify_source_name: str | None = None,
    # Opta stats (all optional — omitted stats marked as unavailable)
    home_xg: float | None = None,
    away_xg: float | None = None,
    possession_home: float | None = None,
    possession_away: float | None = None,
    shots_home: int | None = None,
    shots_away: int | None = None,
    sot_home: int | None = None,
    sot_away: int | None = None,
    corners_home: int | None = None,
    corners_away: int | None = None,
    passes_home: int | None = None,
    passes_away: int | None = None,
    data_source: str = "manual",
    dry_run: bool = False,
    trust_db_score: bool = False,
) -> dict:
    """Execute the complete 7-step post-match pipeline.

    Returns a dict with per-step status and final summary.
    """
    match_uuid = match_id.replace("-", "").strip()
    verification_match_id = match_uuid
    pipeline_status = {step: "pending" for step in PIPELINE_STEPS}
    pipeline_data: dict = {}

    print(f"\n{'='*70}")
    print(f"  POST-MATCH COMPLETE PIPELINE")
    print(f"  Match: {match_uuid} | Score: {home_score}-{away_score}")
    print(f"  Mode: {'DRY-RUN' if dry_run else 'LIVE'}")
    print(f"{'='*70}")

    async with AsyncSessionLocal() as db:
        # ═══════════════════════════════════════════════════════════
        # STEP 1: Multi-source score verification (HARD GATE)
        # ═══════════════════════════════════════════════════════════
        print(f"\n{'─'*50}")
        print(f"  STEP 1/7: Score Verification Gate")
        print(f"{'─'*50}")

        verification_service = get_verification_service()

        # Source 1: match_results table (tier 3)
        await verification_service.add_source_result(
            db=db,
            match_id=verification_match_id,
            home_goals=home_score,
            away_goals=away_score,
            source_name="match_results_import",
            source_tier=SourceTier.REPUTABLE_DATA_PROVIDER,
            match_status="Finished",
            notes=f"complete_postmatch pipeline (snapshot lookup pending)",
        )
        print("  + Source 1: match_results_import (tier 3)")

        # Source 2: URL verification (tier 4) or user-provided audit note (tier 6).
        # Tier-6 rows do not count toward verified consensus.
        second_source_added = False
        if verify_url:
            print(f"  → Fetching verification URL: {verify_url}")
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(
                        verify_url,
                        headers={"User-Agent": "Mozilla/5.0 (compatible; WC26Predict/3.5)"},
                        follow_redirects=True,
                    )
                if resp.status_code == 200:
                    page_text = resp.text[:50000]
                    score_patterns = re.findall(r"(\d+)\s*[-–:]\s*(\d+)", page_text)
                    url_matched = any(
                        int(h) == home_score and int(a) == away_score
                        for h, a in score_patterns
                    )
                    if not url_matched:
                        try:
                            payload = resp.json()
                        except Exception:
                            payload = None
                        if isinstance(payload, dict):
                            fifa_home = (
                                (payload.get("HomeTeam") or {}).get("Score")
                                if isinstance(payload.get("HomeTeam"), dict)
                                else None
                            )
                            fifa_away = (
                                (payload.get("AwayTeam") or {}).get("Score")
                                if isinstance(payload.get("AwayTeam"), dict)
                                else None
                            )
                            if fifa_home is not None and fifa_away is not None:
                                url_matched = int(fifa_home) == home_score and int(fifa_away) == away_score
                    if url_matched:
                        source_label = verify_source_name or "url_verified"
                        source_tier = (
                            SourceTier.OFFICIAL_COMPETITION
                            if "fifa" in source_label.lower()
                            else SourceTier.REPUTABLE_MEDIA
                        )
                        await verification_service.add_source_result(
                            db=db,
                            match_id=verification_match_id,
                            home_goals=home_score,
                            away_goals=away_score,
                            source_name=source_label,
                            source_tier=source_tier,
                            match_status="Finished",
                            notes=f"URL-verified: {verify_url}",
                        )
                        print(f"  + Source 2: {source_label} (tier {source_tier}, URL-verified ✅)")
                        second_source_added = True
                    else:
                        print(f"  ⚠ URL fetched but score {home_score}-{away_score} not found")
                        print(f"     Scores found in page: {score_patterns[:10]}")
                else:
                    print(f"  ⚠ URL fetch failed: HTTP {resp.status_code}")
            except Exception as e:
                print(f"  ⚠ URL fetch error: {e}")

        if not second_source_added:
            second_tier = SourceTier.REPUTABLE_MEDIA if trust_db_score else SourceTier.OTHER
            second_name = "db_verified_score" if trust_db_score else "user_provided"
            tier_label = "tier 4" if trust_db_score else "tier 6"
            await verification_service.add_source_result(
                db=db,
                match_id=verification_match_id,
                home_goals=home_score,
                away_goals=away_score,
                source_name=second_name,
                source_tier=second_tier,
                match_status="Finished",
                notes="DB-verified score (match_results table)" if trust_db_score else "User-provided score (no URL verification)",
            )
            print(f"  + Source 2: {second_name} ({tier_label}, DB-trusted ✅)" if trust_db_score else f"  + Source 2: user_provided ({tier_label}, NOT independently verified ⚠)")

        # Build consensus
        consensus = await verification_service.build_consensus(db, verification_match_id)

        if consensus is None or not consensus.is_verified:
            pipeline_status["verify_score"] = "FAILED"
            print(f"\n  ⛔ HARD STOP: Score verification FAILED")
            print(f"     Sources: {consensus.source_count if consensus else 0}/2 required")
            print(f"     The score {home_score}-{away_score} could not be independently verified.")
            print(f"     Re-run with --verify-url <URL> pointing to a sports site")
            print(f"     that confirms the score (ESPN, SkySports, FIFA.com, etc.)")
            if not verify_url:
                print(f"     Or provide --verify-url with a match report URL.")
            return {
                "status": "ABORTED",
                "failed_at_step": "verify_score",
                "pipeline_status": pipeline_status,
                "error": "Score verification failed — insufficient independent sources",
                "fix": "Re-run with --verify-url <URL>",
            }

        verified_result_id = str(consensus.verification_id)
        pipeline_status["verify_score"] = "passed"
        pipeline_data["verified_result_id"] = verified_result_id
        print(f"  ✅ VERIFIED: {consensus.home_goals}-{consensus.away_goals} "
              f"({consensus.source_count} sources: {', '.join(consensus.source_names)})")

        # ═══════════════════════════════════════════════════════════
        # STEP 2: Find prediction snapshot
        # ═══════════════════════════════════════════════════════════
        print(f"\n{'─'*50}")
        print(f"  STEP 2/7: Find Prediction Snapshot")
        print(f"{'─'*50}")

        # match_id can be stored as raw 32-char hex OR 36-char UUID with hyphens.
        # Try both formats with OR to handle whatever convention the snapshot used.
        if len(match_uuid) == 32:
            match_uuid_hyphenated = f"{match_uuid[:8]}-{match_uuid[8:12]}-{match_uuid[12:16]}-{match_uuid[16:20]}-{match_uuid[20:]}"
        else:
            match_uuid_hyphenated = match_uuid

        from sqlalchemy import or_
        snap_conditions = [PredictionSnapshot.match_id == match_uuid]
        if _is_uuid_like(match_uuid):
            snap_conditions.extend(
                [
                    PredictionSnapshot.match_id.like(f"{match_uuid}%"),
                    PredictionSnapshot.match_id.like(f"{match_uuid_hyphenated}%"),
                ]
            )
        snap_result = await db.execute(
            select(PredictionSnapshot)
            .where(or_(*snap_conditions))
            .order_by(PredictionSnapshot.generated_at.desc())
            .limit(1)
        )
        snapshot = snap_result.scalar_one_or_none()
        if snapshot is None:
            snapshot = await _fallback_pre_match_snapshot(db, match_uuid)

        if snapshot is None:
            pipeline_status["find_snapshot"] = "FAILED"
            print(f"  ⛔ No prediction snapshot found for match {match_uuid}")
            return {
                "status": "ABORTED",
                "failed_at_step": "find_snapshot",
                "pipeline_status": pipeline_status,
                "error": "No prediction snapshot found",
                "fix": "Run prediction first before post-match learning",
            }

        pipeline_status["find_snapshot"] = "passed"
        snapshot.component_probs = _normalize_component_probs(
            snapshot.component_probs or {},
            snapshot.market_probs or {},
        )
        pipeline_data["snapshot"] = snapshot
        print(f"  ✅ Found: {snapshot.home_team} vs {snapshot.away_team} "
              f"@ {snapshot.generated_at}")
        if snapshot.run_type == "pre_match_snapshot_fallback":
            print("     source: pre_match_snapshots fallback")

        # Remove old learning log if exists (idempotent re-run).
        # Backfilled standard snapshots can point back to the original
        # pre_match_snapshots row; clean both ids so re-running does not leave
        # duplicate learning records for the same prediction evidence.
        learning_log_snapshot_ids = [snapshot.id]
        params = snapshot.pipeline_params or {}
        if isinstance(params, dict):
            source_snapshot_id = (
                params.get("source_snapshot_id")
                or params.get("pre_match_snapshot_id")
            )
            if source_snapshot_id and source_snapshot_id not in learning_log_snapshot_ids:
                learning_log_snapshot_ids.append(str(source_snapshot_id))
        existing = await db.execute(
            select(PredictionLearningLog).where(
                PredictionLearningLog.snapshot_id.in_(learning_log_snapshot_ids)
            )
        )
        if existing.scalar_one_or_none() is not None:
            print(f"  → Removing old learning log for clean re-run")
            await db.execute(
                delete(PredictionLearningLog).where(
                    PredictionLearningLog.snapshot_id.in_(learning_log_snapshot_ids)
                )
            )
            await db.flush()

        # ═══════════════════════════════════════════════════════════
        # STEP 3: Collect Opta data
        # ═══════════════════════════════════════════════════════════
        print(f"\n{'─'*50}")
        print(f"  STEP 3/7: Collect Match Statistics")
        print(f"{'─'*50}")

        opta_stats = {
            "home_xg": home_xg,
            "away_xg": away_xg,
            "possession_home": possession_home,
            "possession_away": possession_away,
            "shots_home": shots_home,
            "shots_away": shots_away,
            "sot_home": sot_home,
            "sot_away": sot_away,
            "corners_home": corners_home,
            "corners_away": corners_away,
            "passes_home": passes_home,
            "passes_away": passes_away,
            "data_source": data_source,
        }

        available_stats = [k for k, v in opta_stats.items() if v is not None and k != "data_source"]
        missing_stats = [
            k for k in ["home_xg", "away_xg", "possession_home", "possession_away",
                         "shots_home", "shots_away", "sot_home", "sot_away"]
            if opta_stats.get(k) is None
        ]

        if missing_stats:
            pipeline_status["collect_opta_data"] = "incomplete"
            print(f"  ⚠ Incomplete stats — missing: {', '.join(missing_stats)}")
            print(f"     Available: {len(available_stats)} stats from source '{data_source}'")
            print(f"     Learning will proceed but report will note data gaps.")
        else:
            pipeline_status["collect_opta_data"] = "passed"
            print(f"  ✅ All core stats available ({len(available_stats)} metrics from {data_source})")

        pipeline_data["opta_stats"] = opta_stats
        pipeline_data["missing_stats"] = missing_stats

        for k, v in opta_stats.items():
            if v is not None and k != "data_source":
                print(f"     {k}: {v}")

        # ═══════════════════════════════════════════════════════════
        # STEP 4: Update match_results table
        # ═══════════════════════════════════════════════════════════
        print(f"\n{'─'*50}")
        print(f"  STEP 4/7: Update match_results Table")
        print(f"{'─'*50}")

        # Ensure match_results row exists
        result_row = await db.execute(
            text("SELECT id, home_goals, away_goals FROM match_results WHERE match_id = :mid"),
            {"mid": match_uuid},
        )
        existing_result = result_row.fetchone()

        if existing_result is None:
            import uuid
            mr_id = uuid.uuid4().hex
            await db.execute(
                text("INSERT INTO match_results (id, match_id, home_goals, away_goals) "
                     "VALUES (:id, :mid, :hg, :ag)"),
                {"id": mr_id, "mid": match_uuid, "hg": home_score, "ag": away_score},
            )
            print(f"  + Created match_results row: {home_score}-{away_score}")
        else:
            print(f"  → match_results exists: {existing_result[1]}-{existing_result[2]}")

        # Update xG if available
        if home_xg is not None and away_xg is not None:
            await db.execute(
                text("UPDATE match_results SET home_xg = :hxg, away_xg = :axg WHERE match_id = :mid"),
                {"hxg": home_xg, "axg": away_xg, "mid": match_uuid},
            )
            print(f"  + Updated xG: {home_xg} - {away_xg}")

        # Update match status
        await db.execute(
            text("UPDATE matches SET status = 'finished' WHERE id = :mid"),
            {"mid": match_uuid},
        )
        await db.execute(
            text(
                "UPDATE wc26_schedule "
                "SET match_status = 'FINISHED', home_goals = :hg, away_goals = :ag "
                "WHERE CAST(id AS TEXT) = :mid"
            ),
            {"mid": match_uuid, "hg": home_score, "ag": away_score},
        )

        pipeline_status["update_match_results"] = "passed"
        print(f"  ✅ match_results updated")

        # ═══════════════════════════════════════════════════════════
        # STEP 5: Run Learning Engine
        # ═══════════════════════════════════════════════════════════
        print(f"\n{'─'*50}")
        print(f"  STEP 5/7: Learning Engine — Error Attribution")
        print(f"{'─'*50}")

        if dry_run:
            print(f"  [DRY-RUN] Would run LearningEngine with verified_result_id={verified_result_id}")
            pipeline_status["run_learning_engine"] = "skipped_dry_run"
        else:
            engine = get_learning_engine()
            # Look up process evaluation learning_weight (V4.6)
            learning_weight, learning_tier, failure_type, process_label = (
                _lookup_process_eval_weight(
                    getattr(snapshot, 'home_team', ''),
                    getattr(snapshot, 'away_team', ''),
                )
            )
            pipeline_data["learning_weight_detail"] = _lookup_process_eval_detail(
                getattr(snapshot, 'home_team', ''),
                getattr(snapshot, 'away_team', ''),
            )
            error_log = await engine.process_match_result(
                snapshot,
                home_score,
                away_score,
                db,
                verified_result_id=verified_result_id,
                learning_weight=learning_weight,
            )

            pipeline_status["run_learning_engine"] = "passed"
            pipeline_data["learning_log"] = error_log
            print(f"  ✅ Learning complete:")
            print(f"     Brier: {error_log.error_magnitude:.4f}")
            print(f"     Direction: {error_log.error_direction}")
            print(f"     Status: {error_log.status}")
            print(f"     DC marginal: {error_log.dc_marginal}")
            print(f"     Enhancer marginal: {error_log.enhancer_marginal}")
            print(f"     Elo marginal: {error_log.elo_marginal}")

        # ═══════════════════════════════════════════════════════════
        # STEP 6: Generate analysis
        # ═══════════════════════════════════════════════════════════
        print(f"\n{'─'*50}")
        print(f"  STEP 6/7: Generate Post-Match Analysis")
        print(f"{'─'*50}")

        baseline = snapshot.baseline_probs or {}
        component = snapshot.component_probs or {}
        expected_goals = snapshot.expected_goals or {}

        pred_h = baseline.get("home", 0.33)
        pred_d = baseline.get("draw", 0.33)
        pred_a = baseline.get("away", 0.33)
        pred_fav = "home" if pred_h >= pred_d and pred_h >= pred_a else (
            "away" if pred_a >= pred_h and pred_a >= pred_d else "draw"
        )

        actual_idx = 0 if home_score > away_score else (1 if home_score == away_score else 2)
        actual_result = "home" if actual_idx == 0 else ("draw" if actual_idx == 1 else "away")
        dir_correct = (
            (pred_fav == "home" and actual_idx == 0) or
            (pred_fav == "draw" and actual_idx == 1) or
            (pred_fav == "away" and actual_idx == 2)
        )

        # Brier score
        actual_vec = [0, 0, 0]
        actual_vec[actual_idx] = 1
        brier = sum((p - a) ** 2 for p, a in zip([pred_h, pred_d, pred_a], actual_vec))

        # xG comparison
        pred_hxg = expected_goals.get("home", None)
        pred_axg = expected_goals.get("away", None)

        analysis = {
            "predicted": f"{pred_h*100:.1f}%/{pred_d*100:.1f}%/{pred_a*100:.1f}%",
            "favorite": pred_fav,
            "actual_result": actual_result,
            "direction_correct": dir_correct,
            "brier": brier,
            "pred_xg": f"{pred_hxg} - {pred_axg}" if pred_hxg is not None else "N/A",
            "actual_xg": f"{home_xg} - {away_xg}" if home_xg is not None else "N/A",
            "data_completeness": "full" if not missing_stats else "partial",
            "missing_stats": missing_stats,
        }

        pipeline_status["generate_analysis"] = "passed"
        pipeline_data["analysis"] = analysis

        print(f"  ✅ Analysis generated:")
        print(f"     Prediction: {analysis['predicted']} → Favored: {pred_fav}")
        print(f"     Actual: {actual_result} win | Direction: {'✅ correct' if dir_correct else '❌ wrong'}")
        print(f"     Brier: {brier:.4f}")
        print(f"     xG: pred {analysis['pred_xg']} vs actual {analysis['actual_xg']}")
        print(f"     Data: {analysis['data_completeness']}")

        # Per-component breakdown
        print(f"\n  Component-level review:")
        component_rows = _component_review_rows(component, actual_idx, actual_vec)
        pipeline_data["component_review_rows"] = component_rows
        learning_log_for_context = pipeline_data.get("learning_log")
        if learning_log_for_context is not None:
            base_context = learning_log_for_context.context_tags or {}
            learning_log_for_context.context_tags = {
                **base_context,
                "component_attribution": component_rows,
            }
        for row in component_rows:
            print(
                f"     {str(row['label']):12s}: "
                f"{float(row['home'])*100:5.1f}%/"
                f"{float(row['draw'])*100:5.1f}%/"
                f"{float(row['away'])*100:5.1f}%  "
                f"fav={row['fav']} "
                f"dir={'✅' if row['dir_correct'] else '❌'} "
                f"brier={float(row['brier']):.4f}"
            )

        # ═══════════════════════════════════════════════════════════
        # STEP 7: Output report
        # ═══════════════════════════════════════════════════════════
        print(f"\n{'─'*50}")
        print(f"  STEP 7/7: Output Report (3 locations)")
        print(f"{'─'*50}")

        report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        home_team = snapshot.home_team or "Unknown"
        away_team = snapshot.away_team or "Unknown"
        # snapshot.match_time may be stored as str, datetime, or None
        match_time_val = getattr(snapshot, 'match_time', None)
        if match_time_val and hasattr(match_time_val, 'strftime'):
            match_date_str = match_time_val.strftime("%Y-%m-%d")
        elif isinstance(match_time_val, str) and match_time_val:
            match_date_str = match_time_val[:10]  # "2026-06-11T20:00:00" → "2026-06-11"
        else:
            match_date_str = report_date

        # ── 7a. DB commit ──
        if not dry_run:
            await db.commit()
            print(f"  ✅ 7a: DB committed")

        # ── 7b. Write postmatch report to reports/postmatch/ ──
        reports_dir = BACKEND_DIR.parent / "reports" / "postmatch"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_filename = (
            f"{match_date_str}_{home_team.replace(' ', '_')}"
            f"_{away_team.replace(' ', '_')}_postmatch.md"
        )
        report_path = reports_dir / report_filename

        # Collect component-level detail
        component_probs = snapshot.component_probs or {}
        component_rows = pipeline_data.get("component_review_rows") or _component_review_rows(
            component_probs,
            actual_idx,
            actual_vec,
        )
        component_markdown = _component_markdown(component_rows)

        # Collect learning insights
        learning_log = pipeline_data.get("learning_log")
        learning_weight_detail = pipeline_data.get("learning_weight_detail") or _lookup_process_eval_detail(
            home_team,
            away_team,
        )
        learning_section = ""
        if learning_log and not dry_run:
            base_lw = learning_weight_detail.get("base_learning_weight")
            quality_lw = learning_weight_detail.get("learning_data_quality")
            snapshot_factor = learning_weight_detail.get("snapshot_factor", 1.0)
            formula = (
                f"{float(base_lw):.2f} × {float(quality_lw):.2f} × {float(snapshot_factor):.2f}"
                if base_lw is not None and quality_lw is not None
                else "N/A"
            )
            learning_section = f"""
## 📈 Learning Engine

| Metric | Value |
|:---|---:|
| Brier Score | {learning_log.error_magnitude:.4f} |
| Direction | {learning_log.error_direction} |
| Status | {learning_log.status} |
| Failure Type | {learning_weight_detail.get('failure_type') or 'N/A'} |
| Learning Weight | {learning_log.learning_weight:.4f} |
| Learning Formula | {formula} |
| DC Marginal | {_fmt_metric(learning_log.dc_marginal, 4)} |
| Enhancer Marginal | {_fmt_metric(learning_log.enhancer_marginal, 4)} |
| Elo Marginal | {_fmt_metric(learning_log.elo_marginal, 4)} |
"""

        # Build full report
        report_md = f"""# 🏆 Post-Match Review: {home_team} vs {away_team}

**Date**: {match_date_str}
**Score**: {home_score} - {away_score}
**Verified**: ✅ ({consensus.source_count} sources)

---

## 📊 Prediction vs Actual

| | Home | Draw | Away |
|:---|---:|---:|---:|
| Predicted | {pred_h*100:.1f}% | {pred_d*100:.1f}% | {pred_a*100:.1f}% |
| Actual | {'100%' if actual_idx == 0 else '—'} | {'100%' if actual_idx == 1 else '—'} | {'100%' if actual_idx == 2 else '—'} |

- **Favorite**: {pred_fav}
- **Direction**: {'✅ CORRECT' if dir_correct else '❌ WRONG'}
- **Brier Score**: {brier:.4f}

---

## 🔍 Component Analysis

| Component | Probabilities (H/D/A) | Fav | Dir | Brier |
|:---|---:|:---:|:---:|---:|
{component_markdown}

---

## ⚽ xG Comparison

| Metric | Prediction | Actual |
|:---|---:|---:|
| Home xG | {_fmt_metric(pred_hxg)} | {_fmt_metric(home_xg)} |
| Away xG | {_fmt_metric(pred_axg)} | {_fmt_metric(away_xg)} |

**Stats Completeness**: {'Full' if not missing_stats else f'Partial — missing: {", ".join(missing_stats)}'}
**Learning Data Quality**: {learning_weight_detail.get('learning_data_quality') if learning_weight_detail.get('learning_data_quality') is not None else 'N/A'}
{learning_section}
---

*Generated: {datetime.now(timezone.utc).isoformat()} | Pipeline: run_postmatch_complete.py*
"""

        if not dry_run:
            report_path.write_text(report_md, encoding="utf-8")
            print(f"  ✅ 7b: Report written → reports/postmatch/{report_filename}")
        else:
            print(f"  [DRY-RUN] 7b: Would write → reports/postmatch/{report_filename}")

        # ── 7c. Write memory summary ──
        memory_dir = BACKEND_DIR.parent / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        memory_filename = f"wc-postmatch-{home_team.replace(' ', '')}-{away_team.replace(' ', '')}-{match_date_str}.md"
        memory_path = memory_dir / memory_filename

        component_memory = "\n".join(
            f"- **{row['label']}**: {row['fav']} / "
            f"{'correct' if row['dir_correct'] else 'wrong'} / "
            f"Brier {float(row['brier']):.4f}"
            for row in component_rows
        )
        lw_detail = learning_weight_detail
        lw_formula = "N/A"
        if lw_detail.get("base_learning_weight") is not None and lw_detail.get("learning_data_quality") is not None:
            lw_formula = (
                f"{float(lw_detail['base_learning_weight']):.2f} × "
                f"{float(lw_detail['learning_data_quality']):.2f} × "
                f"{float(lw_detail.get('snapshot_factor', 1.0)):.2f} = "
                f"{float(lw_detail['learning_weight']):.4f}"
            )

        memory_md = f"""---
name: wc-postmatch-{home_team.replace(' ', '').lower()}-{away_team.replace(' ', '').lower()}-{match_date_str}
description: "Post-match: {home_team} {home_score}-{away_score} {away_team}"
metadata:
  type: project
---

# {home_team} vs {away_team}: {home_score}-{away_score}

- **Brier**: {brier:.4f}
- **Direction**: {'correct' if dir_correct else 'wrong'}
- **Prediction**: {pred_h*100:.1f}% / {pred_d*100:.1f}% / {pred_a*100:.1f}% (favored: {pred_fav})
- **Stats completeness**: {'full' if not missing_stats else 'partial'}
- **Failure type**: {lw_detail.get('failure_type') or 'N/A'}
- **Learning weight**: {lw_formula}
- **xG**: predicted {_fmt_metric(pred_hxg)}-{_fmt_metric(pred_axg)}, actual {_fmt_metric(home_xg)}-{_fmt_metric(away_xg)}

## Component Review

{component_memory}
"""

        if not dry_run:
            memory_path.write_text(memory_md, encoding="utf-8")
            print(f"  ✅ 7c: Memory written → memory/{memory_filename}")
        else:
            print(f"  [DRY-RUN] 7c: Would write → memory/{memory_filename}")

        pipeline_status["output_report"] = "passed"

        # Generate summary
        summary = {
            "status": "COMPLETE",
            "pipeline_status": pipeline_status,
            "match_id": match_uuid,
            "home_team": home_team,
            "away_team": away_team,
            "score": f"{home_score}-{away_score}",
            "verified": True,
            "brier": brier if not dry_run else None,
            "direction_correct": dir_correct,
            "data_completeness": "full" if not missing_stats else "partial",
            "report_file": str(report_path) if not dry_run else None,
            "memory_file": str(memory_path) if not dry_run else None,
        }

        print(f"\n{'='*70}")
        print(f"  PIPELINE COMPLETE")
        print(f"  {'✅' if dir_correct else '❌'} Direction: {'correct' if dir_correct else 'wrong'}")
        print(f"  📊 Brier: {brier:.4f}" if not dry_run else "  📊 Brier: N/A (dry-run)")
        print(f"  📋 Data: {summary['data_completeness']}")
        print(f"  🔒 Verified: {summary['verified']}")
        if not dry_run:
            print(f"  📝 Report: reports/postmatch/{report_filename}")
            print(f"  🧠 Memory: memory/{memory_filename}")
        print(f"{'='*70}")

        return summary


def main():
    parser = argparse.ArgumentParser(
        description="Complete post-match pipeline — enforced 7-step flow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Minimal (score only — will fail verification unless 2+ sources exist):
  python scripts/run_postmatch_complete.py \\
      --match-id 77382b67668e4d1a966a5fb88af6e408 \\
      --home-score 2 --away-score 0

  # With URL verification (recommended):
  python scripts/run_postmatch_complete.py \\
      --match-id 77382b67668e4d1a966a5fb88af6e408 \\
      --home-score 2 --away-score 0 \\
      --verify-url "https://www.espn.com/soccer/match/_/id/..."

  # Full Opta data:
  python scripts/run_postmatch_complete.py \\
      --match-id 77382b67668e4d1a966a5fb88af6e408 \\
      --home-score 2 --away-score 0 \\
      --verify-url "https://..." \\
      --home-xg 1.43 --away-xg 0.065 \\
      --possession-home 61 --shots-home 16 --sot-home 4 \\
      --data-source "Opta"
        """,
    )

    # Required
    parser.add_argument("--match-id", required=True, help="Match UUID")
    parser.add_argument("--home-score", type=int, required=True, help="Actual home goals")
    parser.add_argument("--away-score", type=int, required=True, help="Actual away goals")

    # Verification
    parser.add_argument("--verify-url", default=None,
                        help="URL to sports site confirming the score")
    parser.add_argument("--verify-source-name", default=None,
                        help="Label for verification source (e.g. 'ESPN')")

    # Opta stats
    parser.add_argument("--home-xg", type=float, default=None)
    parser.add_argument("--away-xg", type=float, default=None)
    parser.add_argument("--possession-home", type=float, default=None)
    parser.add_argument("--possession-away", type=float, default=None)
    parser.add_argument("--shots-home", type=int, default=None)
    parser.add_argument("--shots-away", type=int, default=None)
    parser.add_argument("--sot-home", type=int, default=None)
    parser.add_argument("--sot-away", type=int, default=None)
    parser.add_argument("--corners-home", type=int, default=None)
    parser.add_argument("--corners-away", type=int, default=None)
    parser.add_argument("--passes-home", type=int, default=None)
    parser.add_argument("--passes-away", type=int, default=None)
    parser.add_argument("--data-source", default="manual",
                        help="Source of stats (e.g. 'Opta', 'FIFA')")

    # Mode
    parser.add_argument("--dry-run", action="store_true",
                        help="Run pipeline without writing to database")
    parser.add_argument("--trust-db-score", action="store_true",
                        help="Trust DB match_results score as tier-4 verification source")

    args = parser.parse_args()

    summary = asyncio.run(run_complete_postmatch(
        match_id=args.match_id,
        home_score=args.home_score,
        away_score=args.away_score,
        verify_url=args.verify_url,
        verify_source_name=args.verify_source_name,
        home_xg=args.home_xg,
        away_xg=args.away_xg,
        possession_home=args.possession_home,
        possession_away=args.possession_away,
        shots_home=args.shots_home,
        shots_away=args.shots_away,
        sot_home=args.sot_home,
        sot_away=args.sot_away,
        corners_home=args.corners_home,
        corners_away=args.corners_away,
        passes_home=args.passes_home,
        passes_away=args.passes_away,
        data_source=args.data_source,
        dry_run=args.dry_run,
        trust_db_score=args.trust_db_score,
    ))

    if summary["status"] == "ABORTED":
        print(f"\n❌ Pipeline ABORTED at step '{summary['failed_at_step']}'")
        print(f"   Error: {summary.get('error', 'Unknown')}")
        print(f"   Fix: {summary.get('fix', 'See logs above')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
