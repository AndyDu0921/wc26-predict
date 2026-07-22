from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo
from uuid import UUID

from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.exceptions import NotFoundError
from app.models.article_evidence import ArticleEvidence
from app.models.match import Match
from app.models.news_article import NewsArticle
from app.models.news_signal import NewsSignal
from app.models.postmatch_eval import PostmatchEval
from app.models.postmatch_signal_eval import PostmatchSignalEval
from app.models.prediction_run import PredictionRun
from app.rate_limit import limiter
from app.services.football_data_service import FootballDataService
from app.schemas.match import (
    EvidenceItem,
    EvidenceResponse,
    ReviewRunSummary,
    ReviewSignalSummary,
    ReviewSummary,
    ScheduleGroup,
    ScheduleResponse,
)
from app.schemas.prediction import MatchCard, MatchCardPrediction
from app.utils.datetime import utc_now

router = APIRouter(prefix="/matches", tags=["matches"])


def _latest_prediction_subquery():
    return (
        select(
            PredictionRun.match_id,
            func.max(PredictionRun.created_at).label("latest_created_at"),
        )
        .group_by(PredictionRun.match_id)
        .subquery()
    )


def _build_match_cards(rows: list[tuple[Match, PredictionRun | None]]) -> list[MatchCard]:
    cards: list[MatchCard] = []
    for match, prediction in rows:
        competition_code = FootballDataService.competition_name_to_code(match.competition)
        cards.append(
            MatchCard(
                id=match.id,
                match_date=match.match_date,
                competition=match.competition,
                competition_type=str(match.competition_type),
                competition_code=competition_code,
                competition_name_zh=FootballDataService.competition_name_zh(match.competition),
                stage=match.stage,
                venue=match.venue,
                status=str(match.status),
                home_team=match.home_team,
                away_team=match.away_team,
                latest_prediction=(
                    MatchCardPrediction(
                        latest_run_id=prediction.id,
                        home_win_prob=prediction.home_win_prob,
                        draw_prob=prediction.draw_prob,
                        away_win_prob=prediction.away_win_prob,
                        confidence_score=prediction.confidence_score,
                        run_type=prediction.run_type,
                    )
                    if prediction
                    else None
                ),
            )
        )
    return cards


@router.get("/upcoming", response_model=list[MatchCard])
@limiter.limit("60/minute")
async def get_upcoming_matches(
    request: Request,
    competition_type: str | None = Query(default=None),
    competition: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[MatchCard]:
    now = utc_now()
    latest_prediction_subquery = _latest_prediction_subquery()

    future_bound = now + timedelta(days=14)
    filters = _match_filters(
        date_from=now,
        date_to=future_bound,
        competition_type=competition_type,
        competition=competition,
    )
    result = await db.execute(
        select(Match, PredictionRun)
        .outerjoin(
            latest_prediction_subquery,
            latest_prediction_subquery.c.match_id == Match.id,
        )
        .outerjoin(
            PredictionRun,
            and_(
                PredictionRun.match_id == Match.id,
                PredictionRun.created_at == latest_prediction_subquery.c.latest_created_at,
            ),
        )
        .options(selectinload(Match.home_team), selectinload(Match.away_team))
        .where(*filters)
        .order_by(Match.match_date.asc())
    )
    return _build_match_cards(result.all())


@router.get("/schedule", response_model=ScheduleResponse)
@limiter.limit("60/minute")
async def get_schedule_matches(
    request: Request,
    start_date: datetime | None = Query(default=None),
    end_date: datetime | None = Query(default=None),
    stage: str | None = Query(default=None),
    competition_type: str | None = Query(default=None),
    competition: str | None = Query(default=None),
    days_ahead: int = Query(default=60, ge=1, le=180),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ScheduleResponse:
    now = utc_now()
    date_from = start_date or now
    date_to = end_date or (now + timedelta(days=days_ahead))
    latest_prediction_subquery = _latest_prediction_subquery()
    filters = _match_filters(
        date_from=date_from,
        date_to=date_to,
        stage=stage,
        competition_type=competition_type,
        competition=competition,
    )
    total = await db.scalar(select(func.count()).select_from(Match).where(*filters)) or 0
    query = (
        select(Match, PredictionRun)
        .outerjoin(latest_prediction_subquery, latest_prediction_subquery.c.match_id == Match.id)
        .outerjoin(
            PredictionRun,
            and_(
                PredictionRun.match_id == Match.id,
                PredictionRun.created_at == latest_prediction_subquery.c.latest_created_at,
            ),
        )
        .options(selectinload(Match.home_team), selectinload(Match.away_team))
        .where(*filters)
        .order_by(Match.match_date.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    cards = _build_match_cards(result.all())
    shanghai = ZoneInfo("Asia/Shanghai")
    today = now.astimezone(shanghai).date()
    grouped: dict[str, list[MatchCard]] = {}
    for card in cards:
        local_date = card.match_date.astimezone(shanghai).date()
        grouped.setdefault(local_date.isoformat(), []).append(card)
    groups = [
        ScheduleGroup(
            date=date_key,
            date_label=_date_label(datetime.fromisoformat(date_key).date(), today),
            matches=items,
        )
        for date_key, items in grouped.items()
    ]
    total_pages = max(1, math.ceil(total / page_size)) if page_size else 1
    return ScheduleResponse(groups=groups, total=int(total), total_pages=total_pages, current_page=page)


@router.get("/{match_id}", response_model=MatchCard)
@limiter.limit("30/minute")
async def get_match_detail(request: Request, match_id: UUID, db: AsyncSession = Depends(get_db)) -> MatchCard:
    latest_prediction_subquery = _latest_prediction_subquery()
    result = await db.execute(
        select(Match, PredictionRun)
        .outerjoin(
            latest_prediction_subquery,
            latest_prediction_subquery.c.match_id == Match.id,
        )
        .outerjoin(
            PredictionRun,
            and_(
                PredictionRun.match_id == Match.id,
                PredictionRun.created_at == latest_prediction_subquery.c.latest_created_at,
            ),
        )
        .options(selectinload(Match.home_team), selectinload(Match.away_team))
        .where(Match.id == match_id)
    )
    row = result.first()
    if row is None:
        raise NotFoundError("Match not found")
    return _build_match_cards([row])[0]


@router.get("/{match_id}/review", response_model=ReviewSummary)
@limiter.limit("30/minute")
async def get_match_review(request: Request, match_id: UUID, db: AsyncSession = Depends(get_db)) -> ReviewSummary:
    match_result = await db.execute(
        select(Match)
        .options(selectinload(Match.result))
        .where(Match.id == match_id)
    )
    match = match_result.scalar_one_or_none()
    if match is None or match.result is None:
        raise NotFoundError("Review data is not available for this match")

    run_rows = await db.execute(
        select(PredictionRun, PostmatchEval)
        .join(PostmatchEval, PostmatchEval.prediction_run_id == PredictionRun.id)
        .where(PredictionRun.match_id == match_id)
        .order_by(PredictionRun.created_at.asc())
    )
    signal_rows = await db.execute(
        select(PostmatchSignalEval)
        .options(selectinload(PostmatchSignalEval.signal))
        .where(PostmatchSignalEval.match_id == match_id)
        .order_by(PostmatchSignalEval.created_at.asc())
    )

    runs = [
        ReviewRunSummary(
            prediction_run_id=prediction.id,
            run_type=str(prediction.run_type),
            created_at=prediction.created_at,
            predicted_top_score=str(prediction.top3_scores[0]["score"]) if prediction.top3_scores else "n/a",
            actual_score=f"{evaluation.actual_home_goals}:{evaluation.actual_away_goals}",
            brier_score=evaluation.brier_score,
            log_loss=evaluation.log_loss,
            exact_score_hit=evaluation.exact_score_hit,
            top3_hit=evaluation.top3_hit,
        )
        for prediction, evaluation in run_rows.all()
    ]
    signal_reviews = [
        ReviewSignalSummary(
            signal_id=signal_eval.signal_id,
            summary_zh=signal_eval.signal.summary_zh if signal_eval.signal else "",
            signal_type=str(signal_eval.signal.signal_type) if signal_eval.signal else "unknown",
            verdict=str(signal_eval.verdict),
            notes=signal_eval.notes,
        )
        for signal_eval in signal_rows.scalars().unique().all()
    ]

    return ReviewSummary(
        match_id=match.id,
        actual_score=f"{match.result.home_goals}:{match.result.away_goals}",
        actual_result="H" if match.result.home_goals > match.result.away_goals else "D" if match.result.home_goals == match.result.away_goals else "A",
        runs=runs,
        signal_reviews=signal_reviews,
    )


@router.get("/{match_id}/evidence", response_model=EvidenceResponse)
@limiter.limit("30/minute")
async def get_match_evidence(request: Request, match_id: UUID, db: AsyncSession = Depends(get_db)) -> EvidenceResponse:
    evidence_result = await db.execute(
        select(ArticleEvidence)
        .options(selectinload(ArticleEvidence.article), selectinload(ArticleEvidence.signal))
        .where(ArticleEvidence.match_id == match_id)
        .order_by(ArticleEvidence.relevance_score.desc(), ArticleEvidence.created_at.desc())
    )
    evidence_rows = evidence_result.scalars().all()
    total_articles = await db.scalar(
        select(func.count(distinct(NewsArticle.id)))
        .select_from(NewsArticle)
        .join(NewsSignal, NewsSignal.article_id == NewsArticle.id)
        .where(NewsSignal.match_id == match_id)
    ) or 0
    items = [
        EvidenceItem(
            id=row.id,
            article_title=row.article.title if row.article else "",
            source_name=row.article.source_name if row.article else None,
            source_url=row.article.source_url if row.article else "",
            evidence_snippet=row.evidence_snippet,
            published_at=row.article.published_at if row.article else None,
            relevance_score=row.relevance_score,
            signal_summary=row.signal.summary_zh if row.signal else None,
            used_in_article=row.used_in_article,
        )
        for row in evidence_rows
        if row.article is not None
    ]
    return EvidenceResponse(
        match_id=match_id,
        evidence_items=items,
        total_articles_analyzed=int(total_articles),
        evidence_count=len(items),
    )


def _date_label(date_value, today):
    month_day = f"{date_value.month}月{date_value.day}日"
    if date_value == today:
        return f"今天 {month_day}"
    if date_value == today + timedelta(days=1):
        return f"明天 {month_day}"
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][date_value.weekday()]
    return f"{month_day} {weekday}"


def _match_filters(
    *,
    date_from: datetime,
    date_to: datetime,
    stage: str | None = None,
    competition_type: str | None = None,
    competition: str | None = None,
):
    filters = [Match.match_date >= date_from, Match.match_date <= date_to]
    if stage:
        filters.append(Match.stage.ilike(f"%{stage}%"))
    if competition_type:
        filters.append(Match.competition_type == competition_type)
    if competition:
        candidates = {competition}
        if competition_code := FootballDataService.competition_name_to_code(competition):
            candidates.add(FootballDataService.competition_name_from_code(competition_code))
            if competition_code == "WC":
                candidates.add("FIFA World Cup 2026")
        filters.append(Match.competition.in_(sorted(candidates)))
    return filters
