from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.exceptions import AppError
from app.models import Base
from app.models.accuracy_engine import EvidenceItem
from app.models.enums import ReviewStatus
from app.models.news_signal import NewsSignal
from app.models.signal_review_log import SignalReviewLog
from app.routers.admin import review_signal
from app.schemas.admin import SignalReviewRequest


def _signal() -> NewsSignal:
    return NewsSignal(
        id=uuid4(),
        article_id=uuid4(),
        signal_type="injury",
        impact_direction="negative",
        confidence=0.8,
        key_players=[],
        summary_zh="Traceable injury update",
        source_reliability=0.9,
        review_status=ReviewStatus.PENDING,
        enters_model=False,
    )


def _evidence(evidence_id: str) -> EvidenceItem:
    now = datetime.now(timezone.utc)
    return EvidenceItem(
        id=evidence_id,
        evidence_key=f"key-{evidence_id}",
        evidence_type="news",
        source_url="https://example.test/source",
        raw_text_hash="a" * 64,
        fetched_at=now,
        available_at=now,
        reliability_score=0.9,
    )


def test_approved_model_signal_requires_existing_evidence_and_atomic_log(tmp_path):
    async def run() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{(tmp_path / 'review.db').as_posix()}"
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with AsyncSession(engine, expire_on_commit=False) as db:
            signal = _signal()
            evidence = _evidence("evidence-1")
            db.add_all([signal, evidence])
            await db.commit()

            response = await review_signal.__wrapped__(
                request=None,
                signal_id=signal.id,
                payload=SignalReviewRequest(
                    status="approved",
                    enters_model=True,
                    evidence_id=evidence.id,
                    reviewed_by="auditor",
                ),
                db=db,
            )

            await db.refresh(signal)
            review_count = await db.scalar(
                select(func.count()).select_from(SignalReviewLog)
            )
            assert response.status == "ok"
            assert signal.enters_model is True
            assert signal.evidence_id == evidence.id
            assert review_count == 1

        await engine.dispose()

    asyncio.run(run())


def test_approved_model_signal_rejects_fabricated_evidence_id(tmp_path):
    async def run() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{(tmp_path / 'missing-evidence.db').as_posix()}"
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with AsyncSession(engine, expire_on_commit=False) as db:
            signal = _signal()
            db.add(signal)
            await db.commit()

            with pytest.raises(AppError, match="existing Evidence Ledger"):
                await review_signal.__wrapped__(
                    request=None,
                    signal_id=signal.id,
                    payload=SignalReviewRequest(
                        status="approved",
                        enters_model=True,
                        evidence_id="fabricated-id",
                        reviewed_by="auditor",
                    ),
                    db=db,
                )

            await db.refresh(signal)
            review_count = await db.scalar(
                select(func.count()).select_from(SignalReviewLog)
            )
            assert signal.review_status == ReviewStatus.PENDING
            assert signal.enters_model is False
            assert review_count == 0

        await engine.dispose()

    asyncio.run(run())
