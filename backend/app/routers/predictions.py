from __future__ import annotations

import asyncio
import time
import uuid as _uuid
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin_token
from app.exceptions import NotFoundError
from app.models.content_article import ContentArticle
from app.models.news_signal import NewsSignal
from app.models.enums import ReviewStatus
from app.models.prediction_run import PredictionRun
from app.rate_limit import limiter
from app.schemas.admin import TriggerPredictionRequest
from app.schemas.prediction import ApprovedSignalItem, PredictionHistoryItem, PredictionSnapshot, ScoreProbability
from app.services.canonical_prediction_runner import run_canonical_prediction
from app.utils.datetime import utc_now

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("/{match_id}/latest", response_model=PredictionSnapshot)
@limiter.limit("30/minute")
async def get_latest_prediction(request: Request, match_id: UUID, db: AsyncSession = Depends(get_db)) -> PredictionSnapshot:
    result = await db.execute(
        select(PredictionRun)
        .where(PredictionRun.match_id == match_id)
        .order_by(PredictionRun.created_at.desc())
    )
    prediction = result.scalars().first()
    if prediction is None:
        raise NotFoundError("Prediction not found")

    article_result = await db.execute(
        select(ContentArticle)
        .where(ContentArticle.prediction_run_id == prediction.id)
        .order_by(ContentArticle.created_at.desc())
    )
    article = article_result.scalars().first()
    approved_signal_items: list[ApprovedSignalItem] = []
    if prediction.approved_signals:
        approved_signal_items = [ApprovedSignalItem(**item) for item in prediction.approved_signals]
    else:
        signals_result = await db.execute(
            select(NewsSignal)
            .where(
                NewsSignal.match_id == match_id,
                NewsSignal.review_status == ReviewStatus.APPROVED,
            )
            .order_by(NewsSignal.created_at.desc())
        )
        signals = signals_result.scalars().all()
        approved_signal_items = [
            ApprovedSignalItem(
                id=signal.id,
                signal_type=str(signal.signal_type),
                impact_direction=str(signal.impact_direction),
                summary_zh=signal.summary_zh,
                source_reliability=signal.source_reliability,
                confidence=signal.confidence,
                key_players=signal.key_players,
                player_name=signal.player_name,
                claim=signal.claim,
                evidence_snippet=signal.evidence_snippet,
                normalized_availability=signal.normalized_availability,
                expected_minutes_delta=signal.expected_minutes_delta,
                effective_until=signal.effective_until,
                contradiction_risk=signal.contradiction_risk,
                conflict_group_id=signal.conflict_group_id,
                reviewed_at=signal.reviewed_at,
            )
            for signal in signals
        ]

    article_status = "ready"
    if article is None:
        article_status = (
            "generating"
            if _ensure_utc(prediction.created_at) >= utc_now() - timedelta(minutes=10)
            else "unavailable"
        )

    return PredictionSnapshot(
        id=prediction.id,
        match_id=prediction.match_id,
        run_type=prediction.run_type,
        model_version=prediction.model_version,
        as_of_time=prediction.as_of_time,
        created_at=prediction.created_at,
        home_win_prob=prediction.home_win_prob,
        draw_prob=prediction.draw_prob,
        away_win_prob=prediction.away_win_prob,
        home_xg=prediction.home_xg,
        away_xg=prediction.away_xg,
        score_matrix=prediction.score_matrix,
        top3_scores=[ScoreProbability(**item) for item in prediction.top3_scores],
        confidence_score=prediction.confidence_score,
        risk_tags=prediction.risk_tags,
        approved_signals=approved_signal_items,
        input_feature_snapshot=prediction.input_feature_snapshot,
        article_title=article.title if article else None,
        article_body=article.body if article else None,
        article_status=article_status,
    )


@router.get("/{match_id}/history", response_model=list[PredictionHistoryItem])
@limiter.limit("30/minute")
async def get_prediction_history(request: Request, match_id: UUID, db: AsyncSession = Depends(get_db)) -> list[PredictionHistoryItem]:
    result = await db.execute(
        select(PredictionRun)
        .where(PredictionRun.match_id == match_id)
        .order_by(PredictionRun.created_at.desc())
    )
    runs = result.scalars().all()
    return [
        PredictionHistoryItem(
            id=run.id,
            run_type=run.run_type,
            as_of_time=run.as_of_time,
            created_at=run.created_at,
            home_win_prob=run.home_win_prob,
            draw_prob=run.draw_prob,
            away_win_prob=run.away_win_prob,
            home_xg=run.home_xg,
            away_xg=run.away_xg,
            confidence_score=run.confidence_score,
            risk_tags=run.risk_tags,
        )
        for run in runs
    ]


# ── Authenticated background prediction trigger ──

_prediction_jobs: dict[str, dict] = {}  # prediction_id -> {status, match_id, error, created_at}
_prediction_jobs_lock = asyncio.Lock()
_JOB_MAX_AGE_SECONDS = 3600  # auto-clean jobs older than 1 hour


async def _cleanup_stale_jobs() -> None:
    """Remove completed/failed jobs older than _JOB_MAX_AGE_SECONDS."""
    now = time.monotonic()
    stale = [
        jid for jid, info in _prediction_jobs.items()
        if info.get("created_at", 0) < now - _JOB_MAX_AGE_SECONDS
    ]
    for jid in stale:
        _prediction_jobs.pop(jid, None)

@router.post("/{match_id}/trigger-public")
@limiter.limit("2/minute")
async def trigger_prediction_public(
    request: Request,
    match_id: UUID,
    payload: TriggerPredictionRequest,
    _: str = Depends(require_admin_token),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Backward-compatible background trigger; authentication is mandatory."""
    job_id = _uuid.uuid4().hex[:12]
    _prediction_jobs[job_id] = {
        "status": "running", "match_id": str(match_id), "error": None,
        "created_at": time.monotonic(),
    }

    asyncio.create_task(_run_prediction_bg(job_id, match_id, payload.run_type))

    return {"prediction_id": job_id, "match_id": str(match_id), "status": "running"}


@router.get("/{match_id}/status")
async def get_prediction_job_status(match_id: UUID) -> dict:
    """Check if a prediction is currently running for this match."""
    async with _prediction_jobs_lock:
        await _cleanup_stale_jobs()
        # Iterate over a snapshot copy to avoid concurrent modification
        for job_id, info in list(_prediction_jobs.items()):
            if info["match_id"] == str(match_id):
                return {
                    "prediction_id": job_id,
                    "status": info["status"],
                    "has_result": False,
                }
    return {"status": "none", "has_result": False}


async def _run_prediction_bg(job_id: str, match_id: UUID, run_type: str):
    try:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            await run_canonical_prediction(match_id=match_id, run_type=run_type, db=db)
        async with _prediction_jobs_lock:
            if job_id in _prediction_jobs:
                _prediction_jobs[job_id]["status"] = "completed"
    except Exception as e:
        async with _prediction_jobs_lock:
            if job_id in _prediction_jobs:
                _prediction_jobs[job_id]["status"] = "failed"
                _prediction_jobs[job_id]["error"] = str(e)


@router.post("/{match_id}/trigger")
@limiter.limit("100/minute")
async def trigger_prediction(
    request: Request,
    match_id: UUID,
    payload: TriggerPredictionRequest,
    _: str = Depends(require_admin_token),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    run_id = await run_canonical_prediction(match_id=match_id, run_type=payload.run_type, db=db)
    return {"status": "queued", "prediction_run_id": str(run_id)}


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
