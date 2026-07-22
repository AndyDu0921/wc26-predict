from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.logging import get_logger
from app.models import ContentArticle, Match, NewsSignal, PostmatchEval, PredictionRun
from app.models.enums import MatchStatus, PredictionRunType, ReviewStatus
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
from app.version import VERSION
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
    logger.info(
        "news_ingest_task counts=%s pending_before=%s pending_after=%s", ingest_counts, pending_before, pending_after
    )
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
                        "Replacing stale prediction: match=%s run_type=%s old_version=%s -> %s",
                        match.id,
                        run_type,
                        old.model_version,
                        CURRENT_VERSION,
                    )
                    from sqlalchemy import delete as sa_delete

                    await db.execute(sa_delete(PredictionRun).where(PredictionRun.id == old.id))
                    await db.flush()
                await run_canonical_prediction(match_id=match.id, run_type=run_type.value, db=db)
                created += 1
    logger.info("prediction_trigger_task checked_matches=%s created=%s", checked_matches, created)
    return {"checked_matches": checked_matches, "created": created}


async def _postmatch_eval() -> dict[str, int]:
    from app.services.sqlite_paths import assert_canonical_sqlite_alignment

    sync_db_path = assert_canonical_sqlite_alignment()
    pending: dict[str, tuple[int, int]] = {}
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PredictionRun)
            .join(Match, Match.id == PredictionRun.match_id)
            .options(selectinload(PredictionRun.match).selectinload(Match.result))
            .where(Match.status == MatchStatus.FINISHED)
            .order_by(PredictionRun.created_at.desc())
        )
        prediction_runs = result.scalars().all()
        seen_matches: set[str] = set()
        for run in prediction_runs:
            if run.match is None or run.match.result is None:
                continue
            match_key = str(run.match_id)
            if match_key in seen_matches:
                continue
            seen_matches.add(match_key)
            existing = await db.execute(select(PostmatchEval).where(PostmatchEval.prediction_run_id == run.id))
            if existing.scalar_one_or_none() is not None:
                continue
            pending.setdefault(
                match_key,
                (int(run.match.result.home_goals), int(run.match.result.away_goals)),
            )

    from scripts.run_postmatch_complete import run_complete_postmatch

    created = 0
    deferred = 0
    failed = 0
    for match_id, (home_goals, away_goals) in pending.items():
        try:
            summary = await run_complete_postmatch(
                match_id=match_id,
                home_score=home_goals,
                away_score=away_goals,
                data_source="canonical_postmatch_worker",
                dry_run=False,
                trust_db_score=False,
                db_path=sync_db_path,
            )
        except Exception:
            logger.exception("canonical postmatch worker failed match_id=%s", match_id)
            failed += 1
            continue
        if summary.get("status") == "COMPLETE":
            created += 1
        elif summary.get("status") in {"ABORTED", "INCOMPLETE"}:
            deferred += 1
        else:
            failed += 1
    logger.info(
        "postmatch_eval_task canonical created=%s deferred=%s failed=%s",
        created,
        deferred,
        failed,
    )
    return {"created": created, "deferred": deferred, "failed": failed}


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
            .where(PredictionRun.model_version == VERSION)
            .order_by(PostmatchEval.created_at.asc())
        )
        records = [
            {
                "prediction_run_id": str(run.id),
                "home_win_prob": run.home_win_prob,
                "draw_prob": run.draw_prob,
                "away_win_prob": run.away_win_prob,
                "actual_result": getattr(evaluation.actual_result, "value", evaluation.actual_result),
            }
            for run, evaluation in result.all()
        ]
    candidate_root = settings.model_artifact_dir.parent / "artifacts" / "candidates" / "calibrator"
    payload = _materialize_calibrator_candidate(
        records,
        candidate_root=candidate_root,
        model_cohort=VERSION,
    )
    logger.info("retrain_calibrator_task candidate_result=%s", payload)
    return payload


def _materialize_calibrator_candidate(
    records: list[dict[str, object]],
    *,
    candidate_root: str | Path,
    model_cohort: str,
    min_samples: int = 30,
) -> dict[str, object]:
    """Write an immutable calibrator candidate without touching active artifacts."""
    if len(records) < min_samples:
        return {
            "status": "rejected",
            "reason": "insufficient_same_cohort_samples",
            "model_cohort": model_cohort,
            "n_samples": len(records),
            "minimum_samples": min_samples,
            "active_artifact_changed": False,
        }

    stable_records = sorted(
        records,
        key=lambda item: str(item.get("prediction_run_id") or ""),
    )
    encoded = json.dumps(
        stable_records,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    fingerprint = hashlib.sha256(encoded).hexdigest()
    output_dir = Path(candidate_root) / model_cohort / fingerprint[:16]
    artifact_path = output_dir / "calibrator.json"
    manifest_path = output_dir / "candidate.json"
    if artifact_path.is_file() and manifest_path.is_file():
        return {
            "status": "exists",
            "model_cohort": model_cohort,
            "n_samples": len(records),
            "training_fingerprint": fingerprint,
            "artifact_path": str(artifact_path),
            "manifest_path": str(manifest_path),
            "active_artifact_changed": False,
        }

    calibrator = IsotonicCalibrator().fit_from_db_records(stable_records)
    if not calibrator.is_fitted:
        return {
            "status": "rejected",
            "reason": "calibrator_not_fitted",
            "model_cohort": model_cohort,
            "n_samples": len(records),
            "active_artifact_changed": False,
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    calibrator.save(str(artifact_path))
    manifest = {
        "schema_version": "calibrator_candidate.v1",
        "status": "candidate_unvalidated",
        "generated_at": datetime.now(UTC).isoformat(),
        "model_cohort": model_cohort,
        "n_samples": len(records),
        "training_fingerprint": fingerprint,
        "artifact_path": artifact_path.name,
        "calibration_stats": calibrator.calibration_stats(),
        "promotion_evidence": False,
        "active_artifact_changed": False,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        **manifest,
        "artifact_path": str(artifact_path),
        "manifest_path": str(manifest_path),
    }


async def _embed_articles() -> dict[str, int]:
    service = EmbeddingService()
    async with AsyncSessionLocal() as db:
        processed = await service.batch_embed_articles(db, batch_size=20)
    logger.info("embed_articles_task processed=%s", processed)
    return {"processed": processed}
