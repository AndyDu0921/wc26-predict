from __future__ import annotations

import asyncio
import math
from datetime import UTC
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.logging import get_logger
from app.models import ContentArticle, Match, NewsSignal, PostmatchEval, PostmatchSignalEval, PredictionRun
from app.models.enums import MatchResultCode, MatchStatus, PredictionRunType, ReviewStatus, SignalEvalLabel
from app.services.article_generator import ArticleGeneratorService
from app.services.calibration import IsotonicCalibrator
from app.services.embedding_service import EmbeddingService
from app.services.football_data_service import FootballDataService
from app.services.llm_service import SignalExtractorService
from app.services.news_ingest_service import NewsIngestService
from app.services.canonical_prediction_runner import run_canonical_prediction
from app.config import get_settings
from app.utils.datetime import utc_now
from app.utils.task_runs import record_task_run
from app.workers.celery_app import celery_app

logger = get_logger(__name__)
settings = get_settings()


def _run_async(coro):
    return asyncio.run(coro)


@celery_app.task(name="app.workers.tasks.sync_matches_task")
def sync_matches_task() -> dict[str, int]:
    result = _run_async(_sync_matches())
    record_task_run("sync_matches")
    return result


@celery_app.task(name="app.workers.tasks.sync_league_matches_task")
def sync_league_matches_task() -> dict[str, int]:
    result = _run_async(_sync_league_matches())
    record_task_run("sync_league_matches")
    return result


@celery_app.task(name="app.workers.tasks.sync_league_upcoming_task")
def sync_league_upcoming_task() -> dict[str, int]:
    result = _run_async(_sync_league_upcoming())
    record_task_run("sync_league_upcoming")
    return result


@celery_app.task(name="app.workers.tasks.news_ingest_task")
def news_ingest_task() -> dict[str, int]:
    result = _run_async(_news_ingest())
    record_task_run("news_ingest")
    return result


@celery_app.task(name="app.workers.tasks.prediction_trigger_task")
def prediction_trigger_task() -> dict[str, int]:
    result = _run_async(_trigger_predictions())
    record_task_run("prediction_trigger")
    return result


@celery_app.task(name="app.workers.tasks.postmatch_eval_task")
def postmatch_eval_task() -> dict[str, int]:
    result = _run_async(_postmatch_eval())
    record_task_run("postmatch_eval")
    return result


@celery_app.task(name="app.workers.tasks.generate_article_task")
def generate_article_task(prediction_run_id: str) -> dict[str, str]:
    return _run_async(_generate_article(prediction_run_id))


@celery_app.task(name="app.workers.tasks.retrain_calibrator_task")
def retrain_calibrator_task() -> dict[str, object]:
    result = _run_async(_retrain_calibrator())
    record_task_run("retrain_calibrator")
    return result


@celery_app.task(name="app.workers.tasks.embed_articles_task")
def embed_articles_task() -> dict[str, int]:
    result = _run_async(_embed_articles())
    record_task_run("embed_articles")
    return result


@celery_app.task(name="app.workers.tasks.run_predictions_task")
def run_predictions_task() -> dict[str, int]:
    return _run_async(_trigger_predictions())


async def _sync_matches() -> dict[str, int]:
    service = FootballDataService()
    async with AsyncSessionLocal() as db:
        inserted = await service.sync_upcoming_matches(db)
        updated = await service.refresh_finished_scores(db)
    logger.info("sync_matches_task inserted=%s updated=%s", inserted, updated)
    return {"inserted": inserted, "updated": updated}


async def _sync_league_matches() -> dict[str, int]:
    service = FootballDataService()
    async with AsyncSessionLocal() as db:
        inserted = await service.sync_league_matches(db, seasons=[2023, 2024, 2025])
    logger.info("sync_league_matches_task inserted=%s", inserted)
    return {"inserted": inserted}


async def _sync_league_upcoming() -> dict[str, int]:
    service = FootballDataService()
    async with AsyncSessionLocal() as db:
        totals = await service.sync_upcoming_league_matches(db)
    logger.info("sync_league_upcoming_task totals=%s", totals)
    return totals


async def _news_ingest() -> dict[str, int]:
    ingest_service = NewsIngestService()
    extractor = SignalExtractorService()
    async with AsyncSessionLocal() as db:
        ingest_counts = await ingest_service.collect_latest_articles(db)
        pending_before = await db.scalar(
            select(func.count()).select_from(NewsSignal).where(NewsSignal.review_status == ReviewStatus.PENDING)
        )
        await extractor.process_unprocessed_articles(db, batch_size=10)
        pending_after = await db.scalar(
            select(func.count()).select_from(NewsSignal).where(NewsSignal.review_status == ReviewStatus.PENDING)
        )
    logger.info("news_ingest_task counts=%s pending_before=%s pending_after=%s", ingest_counts, pending_before, pending_after)
    return {
        "inserted": ingest_counts["inserted"],
        "event_registry": ingest_counts["event_registry"],
        "gdelt": ingest_counts["gdelt"],
        "rss": ingest_counts["rss"],
        "pending_before": int(pending_before or 0),
        "pending_after": int(pending_after or 0),
    }


async def _trigger_predictions() -> dict[str, int]:
    from app.version import VERSION as CURRENT_VERSION
    now = utc_now()
    created = 0
    checked_matches = 0
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Match)
            .where(
                Match.status == MatchStatus.SCHEDULED,
                Match.match_date >= now,
                Match.match_date <= now + timedelta(hours=24),
            )
            .order_by(Match.match_date.asc())
        )
        matches = result.scalars().all()
        for match in matches:
            checked_matches += 1
            match_date = match.match_date if match.match_date.tzinfo else match.match_date.replace(tzinfo=UTC)
            hours_to_kickoff = (match_date - now).total_seconds() / 3600
            due_run_types: list[PredictionRunType] = []
            if 0 < hours_to_kickoff <= 24:
                due_run_types.append(PredictionRunType.T_MINUS_24H)
            if 0 < hours_to_kickoff <= 3:
                due_run_types.append(PredictionRunType.T_MINUS_3H)

            for run_type in due_run_types:
                existing = await db.execute(
                    select(PredictionRun).where(
                        PredictionRun.match_id == match.id,
                        PredictionRun.run_type == run_type,
                    )
                )
                old = existing.scalar_one_or_none()
                if old is not None:
                    # V4.1.1: allow regeneration when model version changes
                    if old.model_version == CURRENT_VERSION:
                        continue
                    logger.info(
                        "Replacing stale prediction: match=%s run_type=%s "
                        "old_version=%s -> %s",
                        match.id, run_type, old.model_version, CURRENT_VERSION,
                    )
                    from sqlalchemy import delete as sa_delete
                    await db.execute(sa_delete(PredictionRun).where(PredictionRun.id == old.id))
                    await db.flush()
                await run_canonical_prediction(match_id=match.id, run_type=run_type.value, db=db)
                created += 1
    logger.info("prediction_trigger_task checked_matches=%s created=%s", checked_matches, created)
    return {"checked_matches": checked_matches, "created": created}


async def _postmatch_eval() -> dict[str, int]:
    created = 0
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PredictionRun)
            .join(Match, Match.id == PredictionRun.match_id)
            .options(selectinload(PredictionRun.match).selectinload(Match.result))
            .where(Match.status == MatchStatus.FINISHED)
            .order_by(PredictionRun.created_at.asc())
        )
        prediction_runs = result.scalars().all()
        for run in prediction_runs:
            if run.match is None or run.match.result is None:
                continue
            existing = await db.execute(select(PostmatchEval).where(PostmatchEval.prediction_run_id == run.id))
            if existing.scalar_one_or_none() is not None:
                continue

            evaluation = _build_postmatch_eval(run)
            db.add(evaluation)
            await db.flush()
            await _build_signal_evaluations(run, db)
            created += 1
        await db.commit()
    logger.info("postmatch_eval_task created=%s", created)
    return {"created": created}


async def _generate_article(prediction_run_id: str) -> dict[str, str]:
    generator = ArticleGeneratorService()
    async with AsyncSessionLocal() as db:
        run_uuid = UUID(prediction_run_id)
        run_result = await db.execute(
            select(PredictionRun)
            .options(
                selectinload(PredictionRun.match).selectinload(Match.home_team),
                selectinload(PredictionRun.match).selectinload(Match.away_team),
            )
            .where(PredictionRun.id == run_uuid)
        )
        prediction_run = run_result.scalar_one_or_none()
        if prediction_run is None or prediction_run.match is None:
            raise ValueError(f"Prediction run not found: {prediction_run_id}")

        existing_article_result = await db.execute(
            select(ContentArticle)
            .where(ContentArticle.prediction_run_id == prediction_run.id)
            .order_by(ContentArticle.created_at.desc())
            .limit(1)
        )
        existing_article = existing_article_result.scalars().first()
        if existing_article is not None:
            return {"status": "exists", "article_id": str(existing_article.id)}

        signal_ids = [
            UUID(str(item["id"]))
            for item in prediction_run.approved_signals
            if isinstance(item, dict) and item.get("id")
        ]
        approved_signals: list[NewsSignal] = []
        if signal_ids:
            signal_result = await db.execute(select(NewsSignal).where(NewsSignal.id.in_(signal_ids)))
            approved_signals = signal_result.scalars().all()

        article = await generator.generate_article(prediction_run, approved_signals, db)
    logger.info("generate_article_task prediction_run_id=%s article_id=%s", prediction_run_id, article.id)
    return {"status": "generated", "article_id": str(article.id)}


async def _retrain_calibrator() -> dict[str, object]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PredictionRun, PostmatchEval)
            .join(PostmatchEval, PostmatchEval.prediction_run_id == PredictionRun.id)
            .order_by(PostmatchEval.created_at.asc())
        )
        records = [
            {
                "home_win_prob": run.home_win_prob,
                "draw_prob": run.draw_prob,
                "away_win_prob": run.away_win_prob,
                "actual_result": evaluation.actual_result,
            }
            for run, evaluation in result.all()
        ]
    calibrator = IsotonicCalibrator().fit_from_db_records(records)
    stats = calibrator.calibration_stats()
    if calibrator.is_fitted:
        calibrator.save(str(settings.model_artifact_dir.parent / "artifacts" / "calibrator.json"))
        logger.info("retrain_calibrator_task stats=%s", stats)
    else:
        logger.info("retrain_calibrator_task skipped, insufficient records=%s", len(records))
    return stats


async def _embed_articles() -> dict[str, int]:
    service = EmbeddingService()
    async with AsyncSessionLocal() as db:
        processed = await service.batch_embed_articles(db, batch_size=20)
    logger.info("embed_articles_task processed=%s", processed)
    return {"processed": processed}


def _build_postmatch_eval(run: PredictionRun) -> PostmatchEval:
    result = run.match.result
    assert result is not None

    actual_index = 0 if result.home_goals > result.away_goals else 1 if result.home_goals == result.away_goals else 2
    probs = [run.home_win_prob, run.draw_prob, run.away_win_prob]
    actual = [0.0, 0.0, 0.0]
    actual[actual_index] = 1.0
    brier = sum((prob - observed) ** 2 for prob, observed in zip(probs, actual, strict=False))
    log_loss = -math.log(max(probs[actual_index], 1e-12))
    exact_score = f"{result.home_goals}:{result.away_goals}"
    top3_hit = any(item["score"] == exact_score for item in run.top3_scores)
    bucket = min(10, max(1, int(max(probs) * 10) + 1))

    return PostmatchEval(
        prediction_run_id=run.id,
        actual_home_goals=result.home_goals,
        actual_away_goals=result.away_goals,
        actual_result=MatchResultCode.HOME if actual_index == 0 else MatchResultCode.DRAW if actual_index == 1 else MatchResultCode.AWAY,
        brier_score=brier,
        log_loss=log_loss,
        exact_score_hit=bool(run.top3_scores and run.top3_scores[0]["score"] == exact_score),
        top3_hit=top3_hit,
        calibration_bucket=bucket,
        notes="Auto-generated postmatch evaluation",
    )


async def _build_signal_evaluations(run: PredictionRun, db) -> None:
    if not run.approved_signals:
        return
    signal_ids = [item["id"] for item in run.approved_signals if item.get("id")]
    if not signal_ids:
        return
    result = await db.execute(
        select(NewsSignal).where(NewsSignal.id.in_(signal_ids))
    )
    signals = result.scalars().all()
    for signal in signals:
        exists = await db.execute(
            select(PostmatchSignalEval).where(
                PostmatchSignalEval.prediction_run_id == run.id,
                PostmatchSignalEval.signal_id == signal.id,
            )
        )
        if exists.scalar_one_or_none() is not None:
            continue
        verdict = _score_signal_verdict(run, signal)
        db.add(
            PostmatchSignalEval(
                match_id=run.match_id,
                prediction_run_id=run.id,
                signal_id=signal.id,
                verdict=verdict,
                notes="Auto-scored from final result",
            )
        )


def _score_signal_verdict(run: PredictionRun, signal: NewsSignal) -> SignalEvalLabel:
    result = run.match.result
    assert result is not None
    home_won = result.home_goals > result.away_goals
    away_won = result.away_goals > result.home_goals
    team_side = "home" if signal.team_id == run.match.home_team_id else "away" if signal.team_id == run.match.away_team_id else None
    if signal.impact_direction == "neutral" or team_side is None:
        return SignalEvalLabel.NEUTRAL
    if signal.impact_direction == "uncertain":
        return SignalEvalLabel.UNKNOWN
    if team_side == "home":
        if signal.impact_direction == "positive":
            return SignalEvalLabel.ACCURATE if home_won else SignalEvalLabel.MISLEADING
        return SignalEvalLabel.ACCURATE if not home_won else SignalEvalLabel.MISLEADING
    if signal.impact_direction == "positive":
        return SignalEvalLabel.ACCURATE if away_won else SignalEvalLabel.MISLEADING
    return SignalEvalLabel.ACCURATE if not away_won else SignalEvalLabel.MISLEADING
