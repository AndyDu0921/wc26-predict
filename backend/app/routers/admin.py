from __future__ import annotations

import logging
from datetime import UTC, timedelta
from datetime import datetime
from datetime import timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.dependencies import require_admin_token
from app.exceptions import AppError, NotFoundError
from app.models.accuracy_engine import EvidenceItem
from app.models.content_article import ContentArticle
from app.models.feedback import Feedback
from app.models.match import Match
from app.models.match import MatchResult
from app.models.news_article import NewsArticle
from app.models.news_signal import NewsSignal
from app.models.signal_review_log import SignalReviewLog
from app.models.team import Team
from app.models.postmatch_eval import PostmatchEval
from app.models.enums import CompetitionType
from app.models.enums import ReviewStatus
from app.models.enums import MatchStatus
from app.models.enums import TeamType
from app.models.prediction_run import PredictionRun
from app.rate_limit import limiter
from app.schemas.admin import (
    AdminDashboardSummary,
    CalibratorStatus,
    CompetitionBreakdownItem,
    ConflictSignalGroupItem,
    FeedbackStatusUpdateRequest,
    HermesDigestItem,
    HermesDigestResponse,
    ManualMatchCreateRequest,
    ManualMatchCreateResponse,
    ManualSignalCreateRequest,
    MatchResultUpdateRequest,
    PendingArticleItem,
    PendingSignalItem,
    PublishArticleRequest,
    RecentAccuracyItem,
    RecentPredictionVolumeItem,
    SignalReviewRequest,
    TriggerPredictionRequest,
    TriggerPredictionResponse,
)
from app.schemas.common import APIMessage, PaginatedResponse, PaginationMeta
from app.schemas.feedback import FeedbackItem
from app.services.calibration import IsotonicCalibrator
from app.services.artifact_bundle import verified_artifact_path
from app.services.news_ingest_service import NewsIngestService
from app.services.canonical_prediction_runner import run_canonical_prediction
from app.services.football_data_service import FootballDataService
from app.services.team_resolver import TeamResolver
from app.utils.datetime import utc_now
from app.utils.task_runs import read_task_runs
from app.utils.text import normalize_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_token)])
settings = get_settings()


@router.get("/signals/pending", response_model=PaginatedResponse[PendingSignalItem])
@limiter.limit("100/minute")
async def get_pending_signals(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    match_id: UUID | None = None,
    team_id: UUID | None = None,
    signal_type: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[PendingSignalItem]:
    filters = [NewsSignal.review_status == ReviewStatus.PENDING]
    if match_id:
        filters.append(NewsSignal.match_id == match_id)
    if team_id:
        filters.append(NewsSignal.team_id == team_id)
    if signal_type:
        filters.append(NewsSignal.signal_type == signal_type)

    total = await db.scalar(select(func.count()).select_from(NewsSignal).where(*filters)) or 0
    result = await db.execute(
        select(NewsSignal, NewsArticle)
        .join(NewsArticle, NewsArticle.id == NewsSignal.article_id)
        .where(*filters)
        .order_by(NewsSignal.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        PendingSignalItem(
            id=signal.id,
            article_id=article.id,
            match_id=signal.match_id,
            team_id=signal.team_id,
            signal_type=str(signal.signal_type),
            impact_direction=str(signal.impact_direction),
            confidence=signal.confidence,
            summary_zh=signal.summary_zh,
            source_reliability=signal.source_reliability,
            key_players=signal.key_players,
            player_name=signal.player_name,
            claim=signal.claim,
            evidence_snippet=signal.evidence_snippet,
            evidence_id=signal.evidence_id,
            normalized_availability=signal.normalized_availability,
            expected_minutes_delta=signal.expected_minutes_delta,
            effective_until=signal.effective_until,
            contradiction_risk=signal.contradiction_risk,
            conflict_group_id=signal.conflict_group_id,
            created_at=signal.created_at,
            article_title=article.title,
            source_name=article.source_name,
        )
        for signal, article in result.all()
    ]
    return PaginatedResponse(items=items, pagination=PaginationMeta(page=page, page_size=page_size, total=total))


@router.get("/signals/conflicts", response_model=list[ConflictSignalGroupItem])
@limiter.limit("100/minute")
async def get_conflicting_signals(
    request: Request,
    match_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[ConflictSignalGroupItem]:
    filters = [NewsSignal.conflict_group_id.is_not(None)]
    if match_id:
        filters.append(NewsSignal.match_id == match_id)
    result = await db.execute(
        select(NewsSignal, NewsArticle)
        .join(NewsArticle, NewsArticle.id == NewsSignal.article_id)
        .where(*filters)
        .order_by(NewsSignal.conflict_group_id.asc(), NewsSignal.created_at.desc())
    )
    grouped: dict[str, list[PendingSignalItem]] = {}
    for signal, article in result.all():
        conflict_id = signal.conflict_group_id
        if not conflict_id:
            continue
        grouped.setdefault(conflict_id, []).append(
            PendingSignalItem(
                id=signal.id,
                article_id=article.id,
                match_id=signal.match_id,
                team_id=signal.team_id,
                signal_type=str(signal.signal_type),
                impact_direction=str(signal.impact_direction),
                confidence=signal.confidence,
                summary_zh=signal.summary_zh,
                source_reliability=signal.source_reliability,
                key_players=signal.key_players,
                player_name=signal.player_name,
                claim=signal.claim,
                evidence_snippet=signal.evidence_snippet,
                evidence_id=signal.evidence_id,
                normalized_availability=signal.normalized_availability,
                expected_minutes_delta=signal.expected_minutes_delta,
                effective_until=signal.effective_until,
                contradiction_risk=signal.contradiction_risk,
                conflict_group_id=signal.conflict_group_id,
                created_at=signal.created_at,
                article_title=article.title,
                source_name=article.source_name,
            )
        )
    return [
        ConflictSignalGroupItem(conflict_group_id=conflict_group_id, signals=signals)
        for conflict_group_id, signals in grouped.items()
    ]


@router.patch("/signals/{signal_id}/review", response_model=APIMessage)
@limiter.limit("100/minute")
async def review_signal(
    request: Request,
    signal_id: UUID,
    payload: SignalReviewRequest,
    db: AsyncSession = Depends(get_db),
) -> APIMessage:
    """Review a single news signal (approve/reject).

    - Each review **must** target a single signal (no batch approve).
    - Approval with enters_model=True requires an existing Evidence Ledger row.
    - Signal mutation and signal_review_log are committed atomically.
    """
    if payload.status not in ("approved", "rejected"):
        raise AppError("status must be 'approved' or 'rejected'")

    if not payload.reviewed_by or not payload.reviewed_by.strip():
        raise AppError("reviewed_by is required (cannot be empty)")

    result = await db.execute(select(NewsSignal).where(NewsSignal.id == signal_id))
    signal = result.scalar_one_or_none()
    if not signal:
        raise NotFoundError("Signal not found")

    previous_status = str(signal.review_status)
    review_status = ReviewStatus(payload.status)
    evidence_id: str | None = None
    if review_status == ReviewStatus.APPROVED and payload.enters_model:
        evidence_id = str(payload.evidence_id or "").strip()
        if not evidence_id:
            raise AppError(
                "evidence_id is required when an approved signal enters the model"
            )
        evidence_result = await db.execute(
            select(EvidenceItem).where(EvidenceItem.id == evidence_id)
        )
        evidence = evidence_result.scalar_one_or_none()
        if evidence is None:
            raise AppError(
                "evidence_id must reference an existing Evidence Ledger row"
            )
        signal_match_id = str(signal.match_id or "").replace("-", "").lower()
        evidence_match_id = str(evidence.match_id or "").replace("-", "").lower()
        if signal_match_id and evidence_match_id and signal_match_id != evidence_match_id:
            raise AppError("evidence_id belongs to a different match")

    signal.review_status = review_status
    signal.review_notes = payload.notes
    signal.reviewed_by = payload.reviewed_by.strip()
    signal.reviewed_at = datetime.now(timezone.utc)

    if review_status == ReviewStatus.APPROVED and payload.enters_model:
        signal.enters_model = True
        signal.evidence_id = evidence_id
    else:
        signal.enters_model = False
        signal.evidence_id = None

    db.add(
        SignalReviewLog(
            signal_id=signal_id,
            action=payload.status,
            previous_status=previous_status,
            new_status=payload.status,
            reviewer=payload.reviewed_by.strip(),
            notes=payload.notes,
        )
    )
    await db.commit()
    await db.refresh(signal)

    detail_parts = [f"Signal {signal_id} → {payload.status}"]
    if signal.enters_model:
        detail_parts.append(f"(enters model, evidence_id={signal.evidence_id})")
    return APIMessage(status="ok", detail=". ".join(detail_parts))


@router.post("/signals/manual", response_model=APIMessage)
@limiter.limit("100/minute")
async def create_manual_signal(
    request: Request,
    payload: ManualSignalCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> APIMessage:
    service = NewsIngestService()
    signal_id = await service.create_manual_signal(payload, db)
    return APIMessage(status="ok", detail=f"Created signal {signal_id}")


@router.get("/articles/pending", response_model=list[PendingArticleItem])
@limiter.limit("100/minute")
async def get_pending_articles(request: Request, db: AsyncSession = Depends(get_db)) -> list[PendingArticleItem]:
    result = await db.execute(
        select(ContentArticle)
        .where(ContentArticle.is_published.is_(False))
        .order_by(ContentArticle.created_at.desc())
    )
    return [
        PendingArticleItem(
            id=article.id,
            match_id=article.match_id,
            prediction_run_id=article.prediction_run_id,
            title=article.title,
            body=article.body,
            article_version=article.article_version,
            created_at=article.created_at,
        )
        for article in result.scalars().all()
    ]


@router.patch("/articles/{article_id}/publish", response_model=APIMessage)
@limiter.limit("100/minute")
async def publish_article(
    request: Request,
    article_id: UUID,
    payload: PublishArticleRequest,
    db: AsyncSession = Depends(get_db),
) -> APIMessage:
    result = await db.execute(select(ContentArticle).where(ContentArticle.id == article_id))
    article = result.scalar_one_or_none()
    if article is None:
        raise NotFoundError("Article not found")
    article.is_published = True
    article.correction_log = [
        *article.correction_log,
        {
            "action": "publish",
            "notes": payload.notes,
            "published_by": payload.published_by,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    ]
    await db.commit()
    return APIMessage(status="ok", detail="Article published")


@router.get("/dashboard", response_model=AdminDashboardSummary)
@limiter.limit("100/minute")
async def get_dashboard(request: Request, db: AsyncSession = Depends(get_db)) -> AdminDashboardSummary:
    now = utc_now()
    today = now.date()
    new_articles = await db.scalar(
        select(func.count()).select_from(ContentArticle).where(func.date(ContentArticle.created_at) == today)
    ) or 0
    pending_signals = await db.scalar(
        select(func.count()).select_from(NewsSignal).where(NewsSignal.review_status == ReviewStatus.PENDING)
    ) or 0
    prediction_runs = await db.scalar(
        select(func.count()).select_from(PredictionRun).where(func.date(PredictionRun.created_at) == today)
    ) or 0
    total_predictions = await db.scalar(select(func.count()).select_from(PredictionRun)) or 0
    recent_accuracy_rows = await db.execute(
        select(PostmatchEval, PredictionRun)
        .join(PredictionRun, PredictionRun.id == PostmatchEval.prediction_run_id)
        .order_by(PostmatchEval.created_at.desc())
        .limit(5)
    )
    recent_accuracy_rows_all = recent_accuracy_rows.all()
    recent_accuracy = [
        RecentAccuracyItem(
            prediction_run_id=run.id,
            match_id=run.match_id,
            brier_score=evaluation.brier_score,
            log_loss=evaluation.log_loss,
            top3_hit=evaluation.top3_hit,
        )
        for evaluation, run in recent_accuracy_rows_all
    ]
    recent_5_avg_brier = (
        sum(evaluation.brier_score for evaluation, _ in recent_accuracy_rows_all) / len(recent_accuracy_rows_all)
        if recent_accuracy_rows_all
        else None
    )
    last_7_days_avg_brier = await db.scalar(
        select(func.avg(PostmatchEval.brier_score)).where(PostmatchEval.created_at >= now - timedelta(days=7))
    )
    top3_hit_rate_overall = await db.scalar(
        select(
            func.avg(
                case(
                    (PostmatchEval.top3_hit.is_(True), 1.0),
                    else_=0.0,
                )
            )
        )
    )
    match_breakdown_rows = await db.execute(
        select(Match.competition_type, func.count())
        .group_by(Match.competition_type)
    )
    prediction_breakdown_rows = await db.execute(
        select(Match.competition_type, func.count(PredictionRun.id))
        .join(PredictionRun, PredictionRun.match_id == Match.id)
        .group_by(Match.competition_type)
    )
    competition_breakdown: dict[str, CompetitionBreakdownItem] = {
        key: CompetitionBreakdownItem(match_count=0, prediction_count=0)
        for key in ("club", "national", "cup")
    }
    for competition_type_value, match_count in match_breakdown_rows.all():
        key = str(competition_type_value)
        competition_breakdown[key] = CompetitionBreakdownItem(
            match_count=int(match_count),
            prediction_count=competition_breakdown.get(key, CompetitionBreakdownItem(match_count=0, prediction_count=0)).prediction_count,
        )
    for competition_type_value, prediction_count in prediction_breakdown_rows.all():
        key = str(competition_type_value)
        existing = competition_breakdown.get(key, CompetitionBreakdownItem(match_count=0, prediction_count=0))
        competition_breakdown[key] = CompetitionBreakdownItem(
            match_count=existing.match_count,
            prediction_count=int(prediction_count),
        )

    recent_volume_rows = await db.execute(
        select(Match.competition, func.count(PredictionRun.id))
        .join(PredictionRun, PredictionRun.match_id == Match.id)
        .where(PredictionRun.created_at >= now - timedelta(days=7))
        .group_by(Match.competition)
        .order_by(func.count(PredictionRun.id).desc(), Match.competition.asc())
    )
    recent_prediction_counts_7d = [
        RecentPredictionVolumeItem(
            competition=competition,
            competition_zh=FootballDataService.competition_name_zh(competition),
            prediction_count=int(prediction_count),
        )
        for competition, prediction_count in recent_volume_rows.all()
    ]

    calibrator_stats = _active_calibrator_stats()
    fitted_at = calibrator_stats.get("fitted_at")
    if isinstance(fitted_at, str):
        try:
            fitted_at = datetime.fromisoformat(fitted_at)
        except ValueError:
            fitted_at = None

    task_runs = read_task_runs()
    beat_keys = (
        "sync_matches",
        "sync_league_upcoming",
        "news_ingest",
        "prediction_trigger",
        "postmatch_eval",
        "retrain_calibrator",
        "embed_articles",
    )
    beat_tasks_last_run = {
        key: datetime.fromisoformat(task_runs[key]) if task_runs.get(key) else None
        for key in beat_keys
    }
    return AdminDashboardSummary(
        new_articles_today=new_articles,
        pending_signals=pending_signals,
        prediction_runs_today=prediction_runs,
        recent_accuracy=recent_accuracy,
        recent_5_matches_avg_brier_score=float(recent_5_avg_brier) if recent_5_avg_brier is not None else None,
        last_7_days_avg_brier_score=float(last_7_days_avg_brier) if last_7_days_avg_brier is not None else None,
        total_predictions_made=total_predictions,
        top3_hit_rate_overall=float(top3_hit_rate_overall) if top3_hit_rate_overall is not None else None,
        competition_breakdown=competition_breakdown,
        calibrator_status=CalibratorStatus(
            is_fitted=bool(calibrator_stats.get("is_fitted")),
            training_samples=int(calibrator_stats.get("training_samples") or 0),
            fitted_at=fitted_at if isinstance(fitted_at, datetime) else None,
            expected_calibration_error=(
                float(calibrator_stats["expected_calibration_error"])
                if calibrator_stats.get("expected_calibration_error") is not None
                else None
            ),
        ),
        beat_tasks_last_run=beat_tasks_last_run,
        recent_prediction_counts_7d=recent_prediction_counts_7d,
    )


@router.get("/hermes/digest", response_model=HermesDigestResponse)
@limiter.limit("60/minute")
async def get_hermes_digest(request: Request, db: AsyncSession = Depends(get_db)) -> HermesDigestResponse:
    now = utc_now()
    pending_signals = int(
        await db.scalar(
            select(func.count()).select_from(NewsSignal).where(NewsSignal.review_status == ReviewStatus.PENDING)
        )
        or 0
    )
    prediction_runs_today = int(
        await db.scalar(
            select(func.count()).select_from(PredictionRun).where(func.date(PredictionRun.created_at) == now.date())
        )
        or 0
    )

    pending_conflict_groups = int(
        await db.scalar(
            select(func.count(func.distinct(NewsSignal.conflict_group_id))).where(
                NewsSignal.review_status == ReviewStatus.PENDING,
                NewsSignal.conflict_group_id.is_not(None),
            )
        )
        or 0
    )
    pending_articles = int(
        await db.scalar(
            select(func.count()).select_from(ContentArticle).where(ContentArticle.is_published.is_(False))
        )
        or 0
    )
    upcoming_matches_24h = int(
        await db.scalar(
            select(func.count()).select_from(Match).where(
                Match.status == MatchStatus.SCHEDULED,
                Match.match_date >= now,
                Match.match_date <= now + timedelta(hours=24),
            )
        )
        or 0
    )

    task_thresholds = {
        "sync_matches": 26 * 60,
        "sync_league_upcoming": 26 * 60,
        "news_ingest": 90,
        "prediction_trigger": 45,
        "postmatch_eval": 26 * 60,
        "retrain_calibrator": 26 * 60,
        "embed_articles": 90,
    }
    task_runs = read_task_runs()
    stale_tasks: list[dict[str, object]] = []
    watch_items: list[HermesDigestItem] = []
    stale_count = 0

    for task_name, threshold_minutes in task_thresholds.items():
        raw_last_run = task_runs.get(task_name)
        last_run_iso: str | None = None
        age_minutes: int | None = None
        stale = True
        if raw_last_run:
            try:
                parsed_last_run = datetime.fromisoformat(raw_last_run)
                if parsed_last_run.tzinfo is None:
                    parsed_last_run = parsed_last_run.replace(tzinfo=UTC)
                else:
                    parsed_last_run = parsed_last_run.astimezone(UTC)
                last_run_iso = parsed_last_run.isoformat()
                age_minutes = max(0, int((now - parsed_last_run).total_seconds() // 60))
                stale = age_minutes > threshold_minutes
            except ValueError:
                last_run_iso = raw_last_run
                age_minutes = None
                stale = True
        if stale:
            stale_count += 1
            watch_items.append(
                HermesDigestItem(
                    label=task_name,
                    detail=last_run_iso or "尚未记录",
                    tone="urgent",
                )
            )
        stale_tasks.append(
            {
                "name": task_name,
                "last_run": last_run_iso,
                "age_minutes": age_minutes,
                "stale": stale,
            }
        )

    focus_items: list[HermesDigestItem] = []
    if pending_conflict_groups or pending_signals:
        focus_items.append(
            HermesDigestItem(
                label="信号审核",
                detail=f"{pending_signals} 条待审，{pending_conflict_groups} 组冲突",
                tone="warning" if pending_signals or pending_conflict_groups else "neutral",
            )
        )
    if pending_articles:
        focus_items.append(
            HermesDigestItem(label="文章队列", detail=f"{pending_articles} 篇未发布分析稿", tone="warning")
        )
    if upcoming_matches_24h:
        focus_items.append(
            HermesDigestItem(label="24h 赛程", detail=f"未来 24 小时 {upcoming_matches_24h} 场比赛", tone="neutral")
        )

    calibrator_stats = _active_calibrator_stats()
    calibrator_is_fitted = bool(calibrator_stats.get("is_fitted"))
    calibrator_detail = "未训练"
    calibrator_tone = "warning"
    if calibrator_is_fitted:
        calibrator_detail = f"样本 {int(calibrator_stats.get('training_samples') or 0)}"
        if calibrator_stats.get("expected_calibration_error") is not None:
            calibrator_detail += f"，ECE {float(calibrator_stats['expected_calibration_error']):.3f}"
        calibrator_tone = "good"
    focus_items.append(HermesDigestItem(label="校准状态", detail=calibrator_detail, tone=calibrator_tone))

    if stale_count:
        focus_items.append(
            HermesDigestItem(
                label="任务巡检",
                detail=f"{stale_count} 个 Beat 任务超时或未记录",
                tone="urgent",
            )
        )

    attention_level = "normal"
    if stale_count or not calibrator_is_fitted:
        attention_level = "urgent"
    elif pending_conflict_groups or pending_signals or pending_articles or upcoming_matches_24h:
        attention_level = "watch"

    summary_parts = [
        f"待审信号 {pending_signals} 条",
        f"冲突组 {pending_conflict_groups} 组",
        f"待发布文章 {pending_articles} 篇",
        f"24 小时内比赛 {upcoming_matches_24h} 场",
    ]
    if stale_count:
        summary_parts.append(f"{stale_count} 个任务需要检查")

    return HermesDigestResponse(
        generated_at=now,
        attention_level=attention_level,
        summary="，".join(summary_parts) + "。",
        counts={
            "pending_signals": pending_signals,
            "conflict_groups": pending_conflict_groups,
            "pending_articles": pending_articles,
            "upcoming_matches_24h": upcoming_matches_24h,
            "prediction_runs_today": prediction_runs_today,
        },
        focus_items=focus_items,
        watch_items=watch_items,
        stale_tasks=stale_tasks,
        calibrator_status=CalibratorStatus(
            is_fitted=calibrator_is_fitted,
            training_samples=int(calibrator_stats.get("training_samples") or 0),
            fitted_at=(
                datetime.fromisoformat(str(calibrator_stats["fitted_at"]))
                if calibrator_stats.get("fitted_at")
                and isinstance(calibrator_stats.get("fitted_at"), str)
                else calibrator_stats.get("fitted_at")
            ),
            expected_calibration_error=(
                float(calibrator_stats["expected_calibration_error"])
                if calibrator_stats.get("expected_calibration_error") is not None
                else None
            ),
        ),
    )


def _active_calibrator_stats() -> dict[str, object]:
    calibrator = IsotonicCalibrator()
    try:
        calibrator.load(str(verified_artifact_path("calibrator")))
    except Exception as exc:
        logger.error("Active calibrator verification/load failed: %s", exc)
    return calibrator.calibration_stats()


@router.post("/predictions/{match_id}/trigger", response_model=TriggerPredictionResponse)
@limiter.limit("100/minute")
async def admin_trigger_prediction(
    request: Request,
    match_id: UUID,
    payload: TriggerPredictionRequest,
    db: AsyncSession = Depends(get_db),
) -> TriggerPredictionResponse:
    run_id = await run_canonical_prediction(match_id=match_id, run_type=payload.run_type, db=db)
    return TriggerPredictionResponse(prediction_run_id=run_id, status="ok")


@router.post("/matches", response_model=ManualMatchCreateResponse)
@limiter.limit("100/minute")
async def create_manual_match(
    request: Request,
    payload: ManualMatchCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> ManualMatchCreateResponse:
    resolver = TeamResolver()
    competition_code = FootballDataService.competition_name_to_code(payload.competition)
    competition_type = _competition_type_from_code(competition_code)
    team_type = TeamType.CLUB if competition_type in {CompetitionType.CLUB, CompetitionType.CUP} else TeamType.NATIONAL
    home_team = await _resolve_or_create_team(payload.home_team_name, resolver, db, team_type=team_type)
    away_team = await _resolve_or_create_team(payload.away_team_name, resolver, db, team_type=team_type)
    external_id = f"manual_{normalize_text(payload.home_team_name)}_{normalize_text(payload.away_team_name)}_{payload.match_date.isoformat()}"

    result = await db.execute(select(Match).where(Match.external_id == external_id))
    match = result.scalar_one_or_none()
    if match is None:
        match = Match(
            external_id=external_id,
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            match_date=payload.match_date,
            competition=payload.competition,
            competition_type=competition_type,
            stage=payload.stage,
            venue=payload.venue,
            is_neutral_venue=payload.is_neutral_venue,
            competition_weight=payload.competition_weight,
            status=MatchStatus.SCHEDULED,
        )
        db.add(match)
    else:
        match.home_team_id = home_team.id
        match.away_team_id = away_team.id
        match.match_date = payload.match_date
        match.competition = payload.competition
        match.competition_type = competition_type
        match.stage = payload.stage
        match.venue = payload.venue
        match.is_neutral_venue = payload.is_neutral_venue
        match.competition_weight = payload.competition_weight

    await db.commit()
    await db.refresh(match)
    return ManualMatchCreateResponse(match_id=match.id, status="created")


@router.patch("/matches/{match_id}/result", response_model=APIMessage)
@limiter.limit("100/minute")
async def update_manual_match_result(
    request: Request,
    match_id: UUID,
    payload: MatchResultUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> APIMessage:
    match = await db.get(Match, match_id)
    if match is None:
        raise NotFoundError("Match not found")

    result = await db.execute(select(MatchResult).where(MatchResult.match_id == match_id))
    match_result = result.scalar_one_or_none()
    if match_result is None:
        match_result = MatchResult(match_id=match_id, home_goals=payload.home_goals, away_goals=payload.away_goals)
        db.add(match_result)
    else:
        match_result.home_goals = payload.home_goals
        match_result.away_goals = payload.away_goals

    match.status = MatchStatus.FINISHED
    await db.commit()

    try:
        from app.workers.tasks import postmatch_eval_task

        postmatch_eval_task.delay()
    except Exception as exc:
        logger.warning("Failed to dispatch postmatch_eval_task: %s", exc)

    return APIMessage(status="updated", detail="Match result saved")


@router.get("/feedback", response_model=PaginatedResponse[FeedbackItem])
@limiter.limit("100/minute")
async def get_feedback(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[FeedbackItem]:
    query = select(Feedback)
    count_query = select(func.count()).select_from(Feedback)
    if status:
        query = query.where(Feedback.status == status)
        count_query = count_query.where(Feedback.status == status)
    total = await db.scalar(count_query) or 0
    result = await db.execute(
        query
        .order_by(Feedback.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [
        FeedbackItem(
            id=item.id,
            match_id=item.match_id,
            article_id=item.article_id,
            feedback_type=item.feedback_type,
            description=item.description,
            contact=item.contact,
            status=item.status,
            created_at=item.created_at,
        )
        for item in result.scalars().all()
    ]
    return PaginatedResponse(items=items, pagination=PaginationMeta(page=page, page_size=page_size, total=total))


@router.patch("/feedback/{feedback_id}", response_model=APIMessage)
@limiter.limit("100/minute")
async def update_feedback_status(
    request: Request,
    feedback_id: UUID,
    payload: FeedbackStatusUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> APIMessage:
    feedback = await db.get(Feedback, feedback_id)
    if feedback is None:
        raise NotFoundError("Feedback not found")
    feedback.status = payload.status
    await db.commit()
    return APIMessage(status="ok", detail="Feedback updated")


async def _resolve_or_create_team(
    team_name: str,
    resolver: TeamResolver,
    db: AsyncSession,
    *,
    team_type: TeamType = TeamType.NATIONAL,
) -> Team:
    team = await resolver.resolve_team(team_name, db)
    if team is not None:
        team.team_type = team_type
        return team

    team = Team(
        name=team_name,
        name_zh=None,
        fifa_code=None,
        team_type=team_type,
        country=None,
        confederation=None,
        elo_rating=1500.0,
    )
    db.add(team)
    await db.flush()
    await resolver.ensure_aliases(team, [team_name], db, source="manual_admin")
    return team


def _competition_type_from_code(code: str | None) -> CompetitionType:
    if code == "CL":
        return CompetitionType.CUP
    if code in {"PL", "PD", "BL1", "SA", "FL1"}:
        return CompetitionType.CLUB
    return CompetitionType.NATIONAL
