from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Query, Request
from redis import Redis
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.models.match import Match
from app.models.postmatch_eval import PostmatchEval
from app.models.prediction_run import PredictionRun
from app.rate_limit import limiter
from app.schemas.stats import (
    AccuracyByCompetitionItem,
    AccuracyOverall,
    AccuracyStatsResponse,
    RecentPredictionItem,
    RecentPredictionsResponse,
    RecentThirtySummary,
)
from app.services.calibration import IsotonicCalibrator
from app.version import VERSION
from app.services.football_data_service import FootballDataService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stats", tags=["stats"])
settings = get_settings()


async def _cache_get(key: str) -> str | None:
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        return await asyncio.to_thread(redis.get, key)
    except Exception as exc:
        logger.warning("Redis cache get failed for key=%s: %s", key, exc)
        return None


async def _cache_set(key: str, value: dict[str, object], ttl_seconds: int) -> None:
    try:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        payload = json.dumps(value, ensure_ascii=False)
        await asyncio.to_thread(redis.setex, key, ttl_seconds, payload)
    except Exception as exc:
        logger.warning("Redis cache set failed for key=%s: %s", key, exc)
        return


def _competition_zh(competition: str) -> str:
    return FootballDataService.competition_name_zh(competition)


def _prediction_result_code(run: PredictionRun) -> str:
    probabilities = {
        "H": run.home_win_prob,
        "D": run.draw_prob,
        "A": run.away_win_prob,
    }
    return max(probabilities, key=probabilities.get)


def _trend_from_recent_brier(values: list[float]) -> str:
    if len(values) < 10:
        return "stable"
    midpoint = max(1, len(values) // 2)
    newer = values[:midpoint]
    older = values[midpoint:]
    if not older:
        return "stable"
    newer_avg = sum(newer) / len(newer)
    older_avg = sum(older) / len(older)
    if newer_avg < older_avg - 0.01:
        return "improving"
    if newer_avg > older_avg + 0.01:
        return "declining"
    return "stable"


@router.get("/accuracy", response_model=AccuracyStatsResponse)
@limiter.limit("60/minute")
async def get_accuracy_stats(request: Request, db: AsyncSession = Depends(get_db)) -> AccuracyStatsResponse:
    cache_key = "stats:accuracy"
    cached = await _cache_get(cache_key)
    if cached:
        return AccuracyStatsResponse.model_validate_json(cached)

    total_predictions = await db.scalar(select(func.count()).select_from(PostmatchEval)) or 0
    brier_avg = await db.scalar(select(func.avg(PostmatchEval.brier_score)))
    log_loss_avg = await db.scalar(select(func.avg(PostmatchEval.log_loss)))
    top3_hit_rate = await db.scalar(
        select(
            func.avg(
                case(
                    (PostmatchEval.top3_hit.is_(True), 1.0),
                    else_=0.0,
                )
            )
        )
    )
    last_updated = await db.scalar(select(func.max(PostmatchEval.created_at)))

    by_competition_result = await db.execute(
        select(
            Match.competition,
            func.count(PostmatchEval.id).label("total"),
            func.avg(PostmatchEval.brier_score).label("brier_score"),
            func.avg(
                case(
                    (PostmatchEval.top3_hit.is_(True), 1.0),
                    else_=0.0,
                )
            ).label("top3_hit_rate"),
        )
        .join(PredictionRun, PredictionRun.match_id == Match.id)
        .join(PostmatchEval, PostmatchEval.prediction_run_id == PredictionRun.id)
        .group_by(Match.competition)
        .order_by(func.count(PostmatchEval.id).desc(), Match.competition.asc())
    )
    by_competition = [
        AccuracyByCompetitionItem(
            competition=competition,
            competition_zh=_competition_zh(competition),
            total=int(total),
            brier_score=float(brier_score) if brier_score is not None else None,
            top3_hit_rate=float(top3_hit_rate_value) if top3_hit_rate_value is not None else None,
        )
        for competition, total, brier_score, top3_hit_rate_value in by_competition_result.all()
    ]

    recent_rows = await db.execute(
        select(PostmatchEval.brier_score, PostmatchEval.top3_hit)
        .order_by(PostmatchEval.created_at.desc())
        .limit(30)
    )
    recent_items = recent_rows.all()
    recent_brier_values = [float(row.brier_score) for row in recent_items]
    recent_30 = RecentThirtySummary(
        brier_score=(sum(recent_brier_values) / len(recent_brier_values)) if recent_brier_values else None,
        top3_hit_rate=(
            sum(1.0 if row.top3_hit else 0.0 for row in recent_items) / len(recent_items)
            if recent_items
            else None
        ),
        trend=_trend_from_recent_brier(recent_brier_values),
    )

    calibrator = IsotonicCalibrator()
    try:
        calibrator.load(str(settings.model_artifact_dir.parent / "artifacts" / "calibrator.json"))
    except Exception as exc:
        logger.warning("Calibrator load failed: %s", exc)

    latest_model_version = await db.scalar(
        select(PredictionRun.model_version).order_by(PredictionRun.created_at.desc()).limit(1)
    )

    response = AccuracyStatsResponse(
        overall=AccuracyOverall(
            total_predictions=int(total_predictions),
            brier_score_avg=float(brier_avg) if brier_avg is not None else None,
            top3_hit_rate=float(top3_hit_rate) if top3_hit_rate is not None else None,
            log_loss_avg=float(log_loss_avg) if log_loss_avg is not None else None,
            last_updated=last_updated,
        ),
        by_competition=by_competition,
        recent_30=recent_30,
        calibration_applied=calibrator.is_fitted,
        model_version=latest_model_version or VERSION,
    )
    await _cache_set(cache_key, response.model_dump(mode="json"), ttl_seconds=300)
    return response


@router.get("/recent-predictions", response_model=RecentPredictionsResponse)
@limiter.limit("60/minute")
async def get_recent_predictions(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    competition: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> RecentPredictionsResponse:
    cache_key = f"stats:recent_predictions:{competition or 'all'}:{limit}"
    cached = await _cache_get(cache_key)
    if cached:
        return RecentPredictionsResponse.model_validate_json(cached)

    query = (
        select(PostmatchEval, PredictionRun, Match)
        .join(PredictionRun, PredictionRun.id == PostmatchEval.prediction_run_id)
        .join(Match, Match.id == PredictionRun.match_id)
        .options(selectinload(Match.home_team), selectinload(Match.away_team))
        .order_by(PostmatchEval.created_at.desc())
        .limit(limit)
    )
    if competition:
        candidates = {competition}
        if (competition_code := FootballDataService.competition_name_to_code(competition)) is not None:
            candidates.add(FootballDataService.competition_name_from_code(competition_code))
        query = query.where(Match.competition.in_(sorted(candidates)))

    rows = (await db.execute(query)).all()
    items = [
        RecentPredictionItem(
            match_id=match.id,
            match_date=match.match_date,
            home_team_zh=match.home_team.name_zh or match.home_team.name,
            away_team_zh=match.away_team.name_zh or match.away_team.name,
            competition=match.competition,
            competition_zh=_competition_zh(match.competition),
            predicted_home_win=run.home_win_prob,
            predicted_draw=run.draw_prob,
            predicted_away_win=run.away_win_prob,
            top1_score=str(run.top3_scores[0]["score"]) if run.top3_scores else "—",
            actual_home_goals=evaluation.actual_home_goals,
            actual_away_goals=evaluation.actual_away_goals,
            result=(
                "home_win"
                if evaluation.actual_result == "H"
                else "draw"
                if evaluation.actual_result == "D"
                else "away_win"
            ),
            prediction_correct=_prediction_result_code(run) == evaluation.actual_result,
            top3_hit=evaluation.top3_hit,
            brier_score=evaluation.brier_score,
        )
        for evaluation, run, match in rows
    ]
    response = RecentPredictionsResponse(items=items)
    await _cache_set(cache_key, response.model_dump(mode="json"), ttl_seconds=600)
    return response
