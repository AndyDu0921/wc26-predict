"""snapshot_service.py — Save PreMatchSnapshot to database.

Provides a lightweight, synchronous service that can be called from both
the artifact pipeline (sync) and the async pipeline. Designed to never
throw — a failed snapshot save must not block prediction.

Usage:
    from app.services.snapshot_service import save_pre_match_snapshot

    save_pre_match_snapshot(
        home_team="China PR",
        away_team="Thailand",
        ...
    )
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── DB path resolution ──
BACKEND_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BACKEND_DIR / "data" / "local_stage2.db"


def save_pre_match_snapshot(
    *,
    home_team: str,
    away_team: str,
    competition: str,
    is_neutral: bool = False,
    prediction_mode: str = "full",
    match_id: str = "",
    kickoff_at: str = "",
    hours_to_kickoff: float | None = None,
    # ── Input availability ──
    weather_available: bool = False,
    odds_available: bool = False,
    lineup_available: bool = False,
    injury_data_available: bool = False,
    news_signals_available: bool = False,
    # ── Input snapshots ──
    weather_snapshot: dict[str, Any] | None = None,
    odds_snapshot: dict[str, Any] | None = None,
    lineup_snapshot: dict[str, Any] | None = None,
    injury_records: list[dict[str, Any]] | None = None,
    news_signal_ids: list[str] | None = None,
    # ── Model outputs ──
    component_probs: dict[str, Any] | None = None,
    # ── Final prediction ──
    final_home_prob: float = 0.333,
    final_draw_prob: float = 0.334,
    final_away_prob: float = 0.333,
    home_xg: float | None = None,
    away_xg: float | None = None,
    top_scores: list[dict[str, Any]] | None = None,
    fused_score_matrix: list[list[float]] | None = None,
    source_score_matrices: dict[str, list[list[float]]] | None = None,
    # ── Fusion metadata ──
    weight_config_label: str = "",
    weight_config: dict[str, Any] | None = None,
    effective_weights: dict[str, float] | None = None,
    fusion_graph: dict[str, Any] | None = None,
    model_disagreement: float | None = None,
    # ── Market ──
    market_blended: bool = False,
    market_weight_used: float | None = None,
    market_divergence: float | None = None,
    # ── Confidence ──
    confidence: str = "medium",
    confidence_penalty: float = 0.0,
    risk_tags: list[str] | None = None,
    pipeline_status: str = "full",
    # ── Missing ──
    missing_inputs: list[str] | None = None,
    degraded_reasons: list[dict[str, str]] | None = None,
    # ── Versions ──
    code_version: str = "",
    model_version: str = "",
    data_fingerprint: str = "",
    git_commit: str = "",
    # ── Source traceability ──
    source_timestamps: dict[str, str] | None = None,
    odds_snapshot_id: str = "",
    weather_snapshot_id: str = "",
    injury_snapshot_id: str = "",
    # ── Reports ──
    report_markdown: str | None = None,
    llm_analysis: str | None = None,
) -> str | None:
    """Save a pre-match snapshot to the database. Returns the snapshot ID or None on failure.

    This function is deliberately sync (uses sqlite3 directly) so it can be called
    from any context — CLI, Dashboard, or API — without async/await overhead.
    A failure is logged but never propagated.
    """
    import json
    import uuid
    from datetime import datetime, timezone

    import hashlib

    snapshot_id = str(uuid.uuid4())
    freeze_dt = datetime.now(timezone.utc).isoformat()
    if not _is_uuid_like(match_id):
        # Accept numeric schedule IDs (e.g. "177") without resolver lookup
        if _is_numeric_id(match_id):
            # Numeric ID is valid as-is — wc26_schedule.id
            pass  # keep match_id unchanged
        else:
            resolved = _resolve_match_id_best_effort(
                home_team=home_team,
                away_team=away_team,
                competition=competition,
                kickoff_at=kickoff_at,
            )
            if resolved:
                match_id = resolved
            else:
                logger.warning(
                    "PreMatchSnapshot not saved for %s vs %s: missing or invalid match_id=%r",
                    home_team,
                    away_team,
                    match_id,
                )
                return None

    # ── Compute input_hash for tamper-evidence / dedup ──
    input_payload = json.dumps({
        "home_team": home_team,
        "away_team": away_team,
        "competition": competition,
        "is_neutral": is_neutral,
        "weather": weather_snapshot,
        "odds": odds_snapshot,
        "lineup": lineup_snapshot,
        "injuries": injury_records,
        "news_signal_ids": news_signal_ids or [],
        "code_version": code_version,
        "weight_config_label": weight_config_label,
        "mode": prediction_mode,
    }, sort_keys=True, default=str, ensure_ascii=False)
    input_hash = hashlib.sha256(input_payload.encode("utf-8")).hexdigest()

    if not DB_PATH.exists():
        logger.warning(
            "Database not found at %s — snapshot not saved", DB_PATH
        )
        return None

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("PRAGMA journal_mode=WAL")

        # Ensure table exists (idempotent)
        _ensure_table(conn)
        # Ensure new columns exist (safe to call repeatedly)
        run_migration()

        conn.execute(
            """INSERT INTO pre_match_snapshots (
                id, match_id, snapshot_at, kickoff_at, hours_to_kickoff,
                home_team, away_team, competition, is_neutral,
                weather_available, odds_available, lineup_available,
                injury_data_available, news_signals_available,
                weather_snapshot, odds_snapshot, lineup_snapshot, injury_records,
                news_signal_ids, component_probs,
                final_home_prob, final_draw_prob, final_away_prob,
                home_xg, away_xg, top_scores,
                fused_score_matrix, source_score_matrices,
                weight_config_label, weight_config, effective_weights,
                fusion_graph, model_disagreement,
                market_blended, market_weight_used, market_divergence,
                confidence, confidence_penalty, risk_tags, pipeline_status,
                missing_inputs, degraded_reasons,
                code_version, model_version, data_fingerprint,
                git_commit, input_hash, source_timestamps,
                odds_snapshot_id, weather_snapshot_id, injury_snapshot_id,
                prediction_mode, report_markdown, llm_analysis
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                match_id,
                freeze_dt,
                kickoff_at or None,
                hours_to_kickoff,
                home_team,
                away_team,
                competition,
                1 if is_neutral else 0,
                1 if weather_available else 0,
                1 if odds_available else 0,
                1 if lineup_available else 0,
                1 if injury_data_available else 0,
                1 if news_signals_available else 0,
                _json_dump(weather_snapshot),
                _json_dump(odds_snapshot),
                _json_dump(lineup_snapshot),
                _json_dump(injury_records),
                _json_dump(news_signal_ids or []),
                _json_dump(component_probs),
                final_home_prob,
                final_draw_prob,
                final_away_prob,
                home_xg,
                away_xg,
                _json_dump(top_scores or []),
                _json_dump(fused_score_matrix),
                _json_dump(source_score_matrices),
                weight_config_label or None,
                _json_dump(weight_config),
                _json_dump(effective_weights),
                _json_dump(fusion_graph),
                model_disagreement,
                1 if market_blended else 0,
                market_weight_used,
                market_divergence,
                confidence or "medium",
                confidence_penalty,
                _json_dump(risk_tags or []),
                pipeline_status or "full",
                _json_dump(missing_inputs or []),
                _json_dump(degraded_reasons or []),
                code_version or "",
                model_version or None,
                data_fingerprint or None,
                git_commit or None,
                input_hash,
                _json_dump(source_timestamps),
                odds_snapshot_id or None,
                weather_snapshot_id or None,
                injury_snapshot_id or None,
                prediction_mode,
                report_markdown or None,
                llm_analysis or None,
            ),
        )
        conn.commit()

        logger.info(
            "PreMatchSnapshot saved: %s — %s vs %s [%s] status=%s",
            snapshot_id[:8],
            home_team,
            away_team,
            prediction_mode,
            pipeline_status,
        )
        return snapshot_id

    except Exception:
        logger.warning(
            "Failed to save PreMatchSnapshot for %s vs %s",
            home_team,
            away_team,
            exc_info=True,
        )
        return None
    finally:
        conn.close()


def _json_dump(obj: Any) -> str | None:
    """Serialize to JSON string, returning None for empty/None input."""
    import json

    if obj is None:
        return None
    if isinstance(obj, (list, dict)) and len(obj) == 0:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None


def _is_uuid_like(value: str) -> bool:
    """Accept UUIDs stored either dashed or as 32 hex chars."""
    clean = str(value or "").replace("-", "").strip()
    return bool(re.fullmatch(r"[0-9a-fA-F]{32}", clean))


def _is_numeric_id(value: str) -> bool:
    """Accept numeric schedule IDs (e.g. '177' for wc26_schedule.id)."""
    clean = str(value or "").strip()
    return bool(re.fullmatch(r"\d+", clean))


def _resolve_match_id_best_effort(
    *,
    home_team: str,
    away_team: str,
    competition: str,
    kickoff_at: str,
) -> str | None:
    """Resolve match_id without letting resolver failures block prediction."""
    try:
        from app.services.match_resolver import resolve_match_id

        resolved = resolve_match_id(
            home_team=home_team,
            away_team=away_team,
            competition=competition,
            kickoff_at=kickoff_at,
            db_path=DB_PATH,
        )
        return resolved.match_id if resolved else None
    except Exception:
        logger.debug("match_id resolver skipped for PreMatchSnapshot", exc_info=True)
        return None


def run_migration() -> bool:
    """Add any missing columns to existing pre_match_snapshots table.

    Safe to call repeatedly — each ALTER TABLE is wrapped in a try/except
    so it succeeds once and no-ops on subsequent calls.
    """
    if not DB_PATH.exists():
        return False

    new_columns = [
        ("git_commit", "TEXT"),
        ("input_hash", "TEXT"),
        ("source_timestamps", "TEXT"),
        ("odds_snapshot_id", "TEXT"),
        ("weather_snapshot_id", "TEXT"),
        ("injury_snapshot_id", "TEXT"),
        ("fused_score_matrix", "TEXT"),
        ("source_score_matrices", "TEXT"),
    ]

    conn = sqlite3.connect(str(DB_PATH))
    try:
        existing = {r[1] for r in conn.execute("PRAGMA table_info(pre_match_snapshots)")}

        added = 0
        for col_name, col_type in new_columns:
            if col_name not in existing:
                try:
                    conn.execute(f"ALTER TABLE pre_match_snapshots ADD COLUMN {col_name} {col_type}")
                    added += 1
                except sqlite3.OperationalError:
                    pass  # column already exists

        if added:
            conn.commit()
            logger.info("pre_match_snapshots migration: added %d column(s)", added)
        return True
    except Exception:
        logger.debug("Migration check skipped — table may not exist yet", exc_info=True)
        return False
    finally:
        conn.close()


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Create the pre_match_snapshots table if it doesn't exist."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS pre_match_snapshots (
            id TEXT PRIMARY KEY,
            match_id TEXT,
            snapshot_at TEXT,
            kickoff_at TEXT,
            hours_to_kickoff REAL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            competition TEXT NOT NULL,
            is_neutral INTEGER DEFAULT 0,
            weather_available INTEGER DEFAULT 0,
            odds_available INTEGER DEFAULT 0,
            lineup_available INTEGER DEFAULT 0,
            injury_data_available INTEGER DEFAULT 0,
            news_signals_available INTEGER DEFAULT 0,
            weather_snapshot TEXT,
            odds_snapshot TEXT,
            lineup_snapshot TEXT,
            injury_records TEXT,
            news_signal_ids TEXT,
            component_probs TEXT,
            final_home_prob REAL NOT NULL,
            final_draw_prob REAL NOT NULL,
            final_away_prob REAL NOT NULL,
            home_xg REAL,
            away_xg REAL,
            top_scores TEXT,
            fused_score_matrix TEXT,
            source_score_matrices TEXT,
            weight_config_label TEXT,
            weight_config TEXT,
            effective_weights TEXT,
            fusion_graph TEXT,
            model_disagreement REAL,
            market_blended INTEGER DEFAULT 0,
            market_weight_used REAL,
            market_divergence REAL,
            confidence TEXT,
            confidence_penalty REAL,
            risk_tags TEXT,
            pipeline_status TEXT,
            missing_inputs TEXT,
            degraded_reasons TEXT,
            code_version TEXT NOT NULL,
            model_version TEXT,
            data_fingerprint TEXT,
            git_commit TEXT,
            input_hash TEXT,
            source_timestamps TEXT,
            odds_snapshot_id TEXT,
            weather_snapshot_id TEXT,
            injury_snapshot_id TEXT,
            prediction_mode TEXT DEFAULT 'full',
            report_markdown TEXT,
            llm_analysis TEXT
        )"""
    )
