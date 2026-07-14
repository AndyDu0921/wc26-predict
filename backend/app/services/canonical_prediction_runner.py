"""Canonical prediction trigger used by API and workers.

The command-line entrypoint remains ``backend/scripts/predict_match_full.py``.
This module gives application code the same core path: PredictionPipeline first,
then closed-loop persistence materialization. It replaces the legacy async
trigger so API/worker predictions do not bypass snapshots.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import ROOT_DIR
from app.exceptions import NotFoundError
from app.models import Match, PredictionRun
from app.models.enums import PredictionRunType
from app.services.closed_loop_feature_snapshot import (
    persist_feature_snapshot_from_latest_prematch,
)
from app.services.information_state_engine import (
    audit_match_information_state,
    collect_match_evidence,
    extract_information_signals,
    score_information_signals,
)
from app.services.prediction_pipeline import PredictionPipeline
from app.services.sqlite_paths import assert_canonical_sqlite_alignment


async def run_canonical_prediction(
    *,
    match_id: UUID,
    run_type: str,
    db: AsyncSession,
    mode: str = "full",
) -> UUID:
    """Run the canonical prediction pipeline for an app ``matches`` row."""
    sync_db_path = assert_canonical_sqlite_alignment()
    match = await _load_match(db, match_id)
    prediction_run_type = PredictionRunType(run_type)
    as_of_time = _resolve_as_of_time(match, prediction_run_type)
    kickoff_at = _iso(match.match_date)
    home_team = match.home_team.name
    away_team = match.away_team.name

    _materialize_information_state(
        db_path=sync_db_path,
        match_id=str(match.id),
        home_team=home_team,
        away_team=away_team,
        kickoff_at=kickoff_at,
    )

    pipeline = PredictionPipeline.from_artifacts(mode=mode)
    result = pipeline.predict_sync(
        home_team,
        away_team,
        match.competition,
        is_neutral=bool(match.is_neutral_venue),
        mode=mode,
        match_id=str(match.id),
        match_date=kickoff_at,
        venue=match.venue,
        save_snapshot=True,
        enable_market=True,
        enable_weather=True,
    )
    report_path, report_markdown = _write_prediction_report(result)
    run_id = await _insert_prediction_run(
        db,
        match=match,
        result=result,
        run_type=prediction_run_type.value,
        as_of_time=as_of_time,
    )

    # Ensure the independent sqlite repair can write without sharing the
    # request transaction connection.
    await db.commit()
    _materialize_prediction_persistence(str(match.id), db_path=sync_db_path)
    _persist_prediction_report_metadata(
        str(match.id),
        report_path=report_path,
        report_markdown=report_markdown,
        db_path=sync_db_path,
    )
    _materialize_information_state(
        db_path=sync_db_path,
        match_id=str(match.id),
        home_team=home_team,
        away_team=away_team,
        kickoff_at=kickoff_at,
    )
    persist_feature_snapshot_from_latest_prematch(sync_db_path, match_id=str(match.id))

    run = await _latest_prediction_run(db, match.id, run_type=prediction_run_type.value)
    if run is None:
        run = await _latest_prediction_run(db, match.id, run_type=None)
    if run is None:
        raise RuntimeError(f"Canonical prediction did not persist prediction_run for match_id={match.id}")
    return run.id or run_id


async def _load_match(db: AsyncSession, match_id: UUID) -> Match:
    result = await db.execute(
        select(Match)
        .options(selectinload(Match.home_team), selectinload(Match.away_team))
        .where(Match.id == match_id)
    )
    match = result.scalar_one_or_none()
    if match is None:
        raise NotFoundError("Match not found")
    if match.home_team is None or match.away_team is None:
        raise ValueError(f"Match {match_id} is missing team relationships")
    return match


async def _latest_prediction_run(
    db: AsyncSession,
    match_id: UUID,
    *,
    run_type: str | None,
) -> PredictionRun | None:
    stmt = select(PredictionRun).where(PredictionRun.match_id == match_id)
    if run_type:
        stmt = stmt.where(PredictionRun.run_type == run_type)
    result = await db.execute(stmt.order_by(PredictionRun.created_at.desc()))
    return result.scalars().first()


async def _insert_prediction_run(
    db: AsyncSession,
    *,
    match: Match,
    result: Any,
    run_type: str,
    as_of_time: datetime,
) -> UUID:
    payload = result.to_dict()
    prediction = payload.get("prediction", {})
    run = PredictionRun(
        match_id=match.id,
        run_type=run_type,
        model_version=str(result.model_version),
        as_of_time=as_of_time,
        home_win_prob=float(result.home_win_prob),
        draw_prob=float(result.draw_prob),
        away_win_prob=float(result.away_win_prob),
        home_xg=float(result.home_xg),
        away_xg=float(result.away_xg),
        score_matrix=result.score_matrix or [],
        top3_scores=result.top_scores or [],
        confidence_score=float(result.confidence_score),
        risk_tags=list(result.risk_tags or []),
        input_feature_snapshot={
            "schema_version": "prediction_run_feature_snapshot.v2",
            "source": "canonical_prediction_runner",
            "prediction": prediction,
            "component_probs": payload.get("component_probs", {}),
            "missing_inputs": payload.get("missing_inputs", []),
            "source_status": payload.get("source_status", {}),
            "degraded_reasons": payload.get("degraded_reasons", []),
        },
        approved_signals=[
            item for item in (result.active_events or []) if isinstance(item, dict)
        ],
    )
    db.add(run)
    await db.flush()
    return run.id


def _resolve_as_of_time(match: Match, run_type: PredictionRunType) -> datetime:
    kickoff = match.match_date
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=UTC)
    if run_type == PredictionRunType.T_MINUS_24H:
        return kickoff - timedelta(hours=24)
    if run_type == PredictionRunType.T_MINUS_3H:
        return kickoff - timedelta(hours=3)
    return datetime.now(UTC)


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _materialize_information_state(
    *,
    db_path: str | Path,
    match_id: str,
    home_team: str,
    away_team: str,
    kickoff_at: str,
) -> None:
    try:
        collect_match_evidence(
            db_path,
            match_id=match_id,
            home_team=home_team,
            away_team=away_team,
        )
        extract_information_signals(
            db_path,
            match_id=match_id,
            home_team=home_team,
            away_team=away_team,
            kickoff_at=kickoff_at,
            persist=True,
        )
        score_information_signals(
            db_path,
            match_id=match_id,
            home_team=home_team,
            away_team=away_team,
        )
        audit_match_information_state(
            db_path,
            match_id=match_id,
            home_team=home_team,
            away_team=away_team,
            kickoff_at=kickoff_at,
        )
    except Exception:
        # Prediction should still be possible when information collection is
        # degraded; the snapshot records missing inputs for later audit.
        return


def _materialize_prediction_persistence(match_id: str, *, db_path: str | Path) -> None:
    from scripts.backfill_prediction_persistence import repair_match

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        repair_match(conn, match_id, persist=True)


def _write_prediction_report(result: Any) -> tuple[str, str]:
    report_dir = Path(os.environ.get("PREDICTION_REPORT_DIR", ROOT_DIR / "reports" / "predictions"))
    report_dir.mkdir(parents=True, exist_ok=True)
    date_token = _date_token(result.match_date or result.generated_at or datetime.now(UTC).isoformat())
    home = _safe_token(result.home_team)
    away = _safe_token(result.away_team)
    path = report_dir / f"{date_token}_{home}_vs_{away}_prediction.md"
    markdown = _render_prediction_report(result)
    if path.exists():
        try:
            markdown = path.read_text(encoding="utf-8")
        except OSError:
            pass
    else:
        path.write_text(markdown, encoding="utf-8")
    try:
        rel_path = path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        rel_path = str(path.resolve())
    return rel_path, markdown


def _persist_prediction_report_metadata(
    match_id: str,
    *,
    report_path: str,
    report_markdown: str,
    db_path: str | Path,
) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        if _has_table(conn, "prediction_snapshots"):
            row = conn.execute(
                """
                SELECT id
                FROM prediction_snapshots
                WHERE CAST(match_id AS TEXT) = ?
                ORDER BY generated_at DESC, id DESC
                LIMIT 1
                """,
                (match_id,),
            ).fetchone()
            if row is not None:
                conn.execute(
                    """
                    UPDATE prediction_snapshots
                    SET report_path = ?, report_markdown = ?
                    WHERE id = ?
                    """,
                    (report_path, report_markdown, row[0]),
                )
        if _has_table(conn, "pre_match_snapshots"):
            row = conn.execute(
                """
                SELECT id
                FROM pre_match_snapshots
                WHERE CAST(match_id AS TEXT) = ?
                ORDER BY snapshot_at DESC, id DESC
                LIMIT 1
                """,
                (match_id,),
            ).fetchone()
            if row is not None:
                conn.execute(
                    "UPDATE pre_match_snapshots SET report_markdown = ? WHERE id = ?",
                    (report_markdown, row[0]),
                )
        conn.commit()


def _render_prediction_report(result: Any) -> str:
    top_scores = result.top_scores or []
    components = {
        "dc": result.dc_probs,
        "enhancer": result.enhancer_probs,
        "negbin": result.negbin_probs,
        "weibull": result.weibull_probs,
        "elo": result.elo_probs,
        "pi": result.pi_probs,
        "market": result.market_probs,
    }
    lines = [
        f"# Prediction: {result.home_team} vs {result.away_team}",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        f"Match time: {result.match_date or 'unknown'}",
        f"Pipeline: canonical_prediction_runner -> PredictionPipeline",
        "",
        "## Probabilities",
        "",
        "| Outcome | Probability |",
        "|:--|--:|",
        f"| {result.home_team} | {result.home_win_prob:.1%} |",
        f"| Draw | {result.draw_prob:.1%} |",
        f"| {result.away_team} | {result.away_win_prob:.1%} |",
        "",
        "## Expected Goals",
        "",
        f"- {result.home_team}: {float(result.home_xg):.3f}",
        f"- {result.away_team}: {float(result.away_xg):.3f}",
        "",
        "## Top Scores",
        "",
    ]
    if top_scores:
        for item in top_scores[:5]:
            lines.append(f"- {item.get('score', '?')}: {float(item.get('prob', 0.0)):.1%}")
    else:
        lines.append("- unavailable")
    lines.extend(["", "## Components", "", "| Component | H | D | A |", "|:--|--:|--:|--:|"])
    for name, probs in components.items():
        if not isinstance(probs, dict):
            lines.append(f"| {name} | unavailable | unavailable | unavailable |")
            continue
        home = probs.get("home", probs.get("home_win_prob", probs.get("home_prob", 0.0)))
        draw = probs.get("draw", probs.get("draw_prob", 0.0))
        away = probs.get("away", probs.get("away_win_prob", probs.get("away_prob", 0.0)))
        lines.append(f"| {name} | {float(home):.1%} | {float(draw):.1%} | {float(away):.1%} |")
    lines.extend(["", "## Data Quality", ""])
    missing = result.missing_inputs or []
    lines.append(f"- Missing inputs: {', '.join(missing) if missing else 'none'}")
    if result.degraded_reasons:
        lines.append("- Degraded reasons:")
        for item in result.degraded_reasons:
            payload = item.to_dict() if hasattr(item, "to_dict") else dict(item)
            lines.append(
                f"  - {payload.get('source', 'unknown')}: "
                f"{payload.get('reason', 'unknown')} ({payload.get('severity', 'warning')})"
            )
    else:
        lines.append("- Degraded reasons: none")
    lines.append("")
    lines.append("No betting advice. Market data, when present, is used only as research evidence.")
    return "\n".join(lines) + "\n"


def _date_token(value: str) -> str:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return datetime.now(UTC).date().isoformat()


def _safe_token(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value)).strip("_") or "team"


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None
