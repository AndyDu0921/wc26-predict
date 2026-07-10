"""Canonical prediction trigger used by API and workers.

The command-line entrypoint remains ``backend/scripts/predict_match_full.py``.
This module gives application code the same core path: PredictionPipeline first,
then closed-loop persistence materialization. It replaces the legacy async
trigger so API/worker predictions do not bypass snapshots.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import NotFoundError
from app.models import Match, PredictionRun
from app.models.enums import PredictionRunType
from app.services.closed_loop_feature_snapshot import (
    persist_feature_snapshot_from_latest_prematch,
)
from app.services.evaluation_registry import DEFAULT_DB_PATH
from app.services.information_state_engine import (
    audit_match_information_state,
    collect_match_evidence,
    extract_information_signals,
    score_information_signals,
)
from app.services.prediction_pipeline import PredictionPipeline


async def run_canonical_prediction(
    *,
    match_id: UUID,
    run_type: str,
    db: AsyncSession,
    mode: str = "full",
) -> UUID:
    """Run the canonical prediction pipeline for an app ``matches`` row."""
    match = await _load_match(db, match_id)
    prediction_run_type = PredictionRunType(run_type)
    as_of_time = _resolve_as_of_time(match, prediction_run_type)
    kickoff_at = _iso(match.match_date)
    home_team = match.home_team.name
    away_team = match.away_team.name

    _materialize_information_state(
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
    _materialize_prediction_persistence(str(match.id))
    persist_feature_snapshot_from_latest_prematch(DEFAULT_DB_PATH, match_id=str(match.id))

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
    match_id: str,
    home_team: str,
    away_team: str,
    kickoff_at: str,
) -> None:
    try:
        collect_match_evidence(
            DEFAULT_DB_PATH,
            match_id=match_id,
            home_team=home_team,
            away_team=away_team,
        )
        extract_information_signals(
            DEFAULT_DB_PATH,
            match_id=match_id,
            home_team=home_team,
            away_team=away_team,
            kickoff_at=kickoff_at,
            persist=True,
        )
        score_information_signals(
            DEFAULT_DB_PATH,
            match_id=match_id,
            home_team=home_team,
            away_team=away_team,
        )
        audit_match_information_state(
            DEFAULT_DB_PATH,
            match_id=match_id,
            home_team=home_team,
            away_team=away_team,
            kickoff_at=kickoff_at,
        )
    except Exception:
        # Prediction should still be possible when information collection is
        # degraded; the snapshot records missing inputs for later audit.
        return


def _materialize_prediction_persistence(match_id: str) -> None:
    from scripts.backfill_prediction_persistence import repair_match

    db_path = Path(DEFAULT_DB_PATH)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        repair_match(conn, match_id, persist=True)
