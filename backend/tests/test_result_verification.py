"""Tests for ResultVerificationService and verification gate.

Covers:
- ConsensusResult dataclass behaviour
- add_source_result: accept FT, reject HT/Live/Unknown
- build_consensus: 1 source → not verified, 2 sources → verified, conflict → not verified
- is_verified: returns (bool, UUID | None) correctly
- SourceTier constants
- LearningEngine _brier and _result_index helpers
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.base import Base
from app.services.result_verification import (
    ConsensusResult,
    ResultVerificationService,
    SourceTier,
    get_verification_service,
)
from app.services.learning_engine import _brier, _coerce_probs, _result_index


# ── integration helpers ──────────────────────────────────────────────

class _IntegrationFixture:
    """Manages an in-memory SQLite database for integration tests."""

    def __init__(self):
        self.engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        self.service = get_verification_service()
        self.match_id = uuid.uuid4()

    async def setup(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Create a test match (needed for FK constraint)
        home_id = str(uuid.uuid4()).replace("-", "")
        away_id = str(uuid.uuid4()).replace("-", "")
        mid_str = str(self.match_id).replace("-", "")
        async with self.engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO teams (id, name, elo_rating) VALUES (:hid, 'TestHome', 1500.0)"
            ), {"hid": home_id})
            await conn.execute(text(
                "INSERT INTO teams (id, name, elo_rating) VALUES (:aid, 'TestAway', 1500.0)"
            ), {"aid": away_id})
            await conn.execute(text(
                "INSERT INTO matches (id, external_id, home_team_id, away_team_id, "
                "match_date, competition, competition_weight, is_neutral_venue, status) "
                "VALUES (:mid, :eid, :hid, :aid, '2026-06-10', 'Test', 1.0, 1, 'finished')"
            ), {
                "mid": mid_str,
                "eid": f"test:{mid_str}",
                "hid": home_id,
                "aid": away_id,
            })
            await conn.commit()

    async def teardown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    def session(self) -> AsyncSession:
        return AsyncSession(self.engine, expire_on_commit=False)


def _run_async(coro):
    """Run an async coroutine and return its result."""
    return asyncio.run(coro)


# ── unit: ConsensusResult dataclass ──────────────────────────────────

class TestConsensusResult:
    def test_verified(self):
        cr = ConsensusResult(
            match_id=uuid.uuid4(),
            home_goals=3,
            away_goals=0,
            source_count=2,
            source_names=["AFA", "FIFA"],
            is_verified=True,
            verification_id=uuid.uuid4(),
        )
        assert cr.is_verified is True
        assert cr.home_goals == 3
        assert cr.away_goals == 0
        assert cr.source_count == 2

    def test_unverified(self):
        cr = ConsensusResult(
            match_id=uuid.uuid4(),
            home_goals=1,
            away_goals=0,
            source_count=1,
            source_names=["api_football"],
            is_verified=False,
        )
        assert cr.is_verified is False
        assert cr.verification_id is None

    def test_defaults(self):
        cr = ConsensusResult(
            match_id=uuid.uuid4(),
            home_goals=0,
            away_goals=0,
            source_count=0,
        )
        assert cr.source_names == []
        assert cr.is_verified is False
        assert cr.verification_id is None


# ── unit: SourceTier constants ───────────────────────────────────────

class TestSourceTier:
    def test_values_distinct(self):
        tiers = {SourceTier.OFFICIAL_FEDERATION, SourceTier.OFFICIAL_COMPETITION,
                 SourceTier.REPUTABLE_DATA_PROVIDER, SourceTier.REPUTABLE_MEDIA,
                 SourceTier.AGGREGATOR, SourceTier.OTHER}
        assert len(tiers) == 6, "All 6 tiers must be distinct"

    def test_ordering(self):
        assert SourceTier.OFFICIAL_FEDERATION < SourceTier.OFFICIAL_COMPETITION
        assert SourceTier.OFFICIAL_COMPETITION < SourceTier.REPUTABLE_DATA_PROVIDER
        assert SourceTier.REPUTABLE_DATA_PROVIDER < SourceTier.REPUTABLE_MEDIA
        assert SourceTier.REPUTABLE_MEDIA < SourceTier.AGGREGATOR
        assert SourceTier.AGGREGATOR < SourceTier.OTHER


# ── unit: _brier and _result_index helpers ───────────────────────────

class TestBrierScore:
    def test_perfect_prediction(self):
        probs = {"home": 1.0, "draw": 0.0, "away": 0.0}
        assert _brier(probs, 0) == 0.0

    def test_terrible_prediction(self):
        probs = {"home": 1.0, "draw": 0.0, "away": 0.0}
        result = _brier(probs, 2)  # away win happened
        assert 1.0 < result < 2.1  # (1-0)² + (0-0)² + (0-1)² = 2.0

    def test_result_index(self):
        assert _result_index(3, 0) == 0  # home win
        assert _result_index(1, 1) == 1  # draw
        assert _result_index(0, 2) == 2  # away win

    def test_coerce_probs_tolerates_legacy_null_market_payload(self):
        probs = _coerce_probs({"home": None})

        assert probs["home"] == pytest.approx(1 / 3)
        assert probs["draw"] == pytest.approx(1 / 3)
        assert probs["away"] == pytest.approx(1 / 3)
        assert sum(probs.values()) == pytest.approx(1.0)

    def test_coerce_probs_accepts_alternate_field_names(self):
        probs = _coerce_probs({
            "home_win_prob": "0.2",
            "draw_prob": "0.3",
            "away_win_prob": "0.5",
        })

        assert probs == pytest.approx({"home": 0.2, "draw": 0.3, "away": 0.5})


# ── integration: verification flow ───────────────────────────────────

class TestResultVerificationIntegration:
    """End-to-end tests using in-memory SQLite with asyncio.run()."""

    # ── add_source_result ───────────────────────────────────────

    def test_add_source_accepts_ft(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    record = await fix.service.add_source_result(
                        db, fix.match_id, 3, 0, "AFA", SourceTier.OFFICIAL_FEDERATION, "FT"
                    )
                    await db.commit()
                    assert record.home_goals == 3
                    assert record.away_goals == 0
                    assert record.source_name == "AFA"
                    assert record.is_consensus is False
            finally:
                await fix.teardown()
        _run_async(_test())

    def test_add_source_accepts_finished(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    record = await fix.service.add_source_result(
                        db, fix.match_id, 1, 1, "FIFA", SourceTier.OFFICIAL_COMPETITION, "Finished"
                    )
                    await db.commit()
                    assert record.match_status_at_source == "Finished"
            finally:
                await fix.teardown()
        _run_async(_test())

    def test_add_source_is_idempotent_for_same_claim(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    first = await fix.service.add_source_result(
                        db, fix.match_id, 1, 0, "FIFA", SourceTier.OFFICIAL_COMPETITION, "Finished"
                    )
                    second = await fix.service.add_source_result(
                        db, fix.match_id, 1, 0, "FIFA", SourceTier.OFFICIAL_COMPETITION, "Finished"
                    )
                    await db.commit()
                    count = (
                        await db.execute(text("SELECT COUNT(*) FROM match_result_verification"))
                    ).scalar_one()

                    assert str(first.id) == str(second.id)
                    assert count == 1
            finally:
                await fix.teardown()
        _run_async(_test())

    def test_add_source_rejects_ht(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    with pytest.raises(ValueError, match="not a finished status"):
                        await fix.service.add_source_result(
                            db, fix.match_id, 1, 0, "ESPN", SourceTier.REPUTABLE_MEDIA, "HT"
                        )
            finally:
                await fix.teardown()
        _run_async(_test())

    def test_add_source_rejects_live(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    with pytest.raises(ValueError, match="not a finished status"):
                        await fix.service.add_source_result(
                            db, fix.match_id, 1, 0, "LiveScore", SourceTier.AGGREGATOR, "Live"
                        )
            finally:
                await fix.teardown()
        _run_async(_test())

    def test_add_source_rejects_unknown(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    with pytest.raises(ValueError, match="not a finished status"):
                        await fix.service.add_source_result(
                            db, fix.match_id, 0, 0, "Unknown", SourceTier.OTHER, "Unknown"
                        )
            finally:
                await fix.teardown()
        _run_async(_test())

    # ── build_consensus ──────────────────────────────────────────

    def test_single_source_not_verified(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    await fix.service.add_source_result(
                        db, fix.match_id, 3, 0, "AFA", SourceTier.OFFICIAL_FEDERATION, "FT"
                    )
                    await db.commit()

                    consensus = await fix.service.build_consensus(db, fix.match_id)
                    assert consensus is not None
                    assert consensus.is_verified is False
                    assert consensus.source_count == 1
                    assert consensus.verification_id is None
            finally:
                await fix.teardown()
        _run_async(_test())

    def test_two_sources_same_score_verified(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    await fix.service.add_source_result(
                        db, fix.match_id, 3, 0, "AFA", SourceTier.OFFICIAL_FEDERATION, "FT"
                    )
                    await fix.service.add_source_result(
                        db, fix.match_id, 3, 0, "FIFA", SourceTier.OFFICIAL_COMPETITION, "Finished"
                    )
                    await db.commit()

                    consensus = await fix.service.build_consensus(db, fix.match_id)
                    assert consensus.is_verified is True
                    assert consensus.source_count == 2
                    assert consensus.home_goals == 3
                    assert consensus.away_goals == 0
                    assert consensus.verification_id is not None
            finally:
                await fix.teardown()
        _run_async(_test())

    def test_user_provided_does_not_verify_with_one_trusted_source(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    await fix.service.add_source_result(
                        db, fix.match_id, 2, 0, "match_results_import", SourceTier.REPUTABLE_DATA_PROVIDER, "FT"
                    )
                    await fix.service.add_source_result(
                        db, fix.match_id, 2, 0, "user_provided", SourceTier.OTHER, "Finished"
                    )
                    await db.commit()

                    consensus = await fix.service.build_consensus(db, fix.match_id)
                    assert consensus is not None
                    assert consensus.is_verified is False
                    assert consensus.source_count == 1
                    assert consensus.verification_id is None

                    verified, vid = await fix.service.is_verified(db, fix.match_id)
                    assert verified is False
                    assert vid is None
            finally:
                await fix.teardown()
        _run_async(_test())

    def test_duplicate_source_family_does_not_verify(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    await fix.service.add_source_result(
                        db, fix.match_id, 1, 1, "FIFA", SourceTier.OFFICIAL_COMPETITION, "FT"
                    )
                    await fix.service.add_source_result(
                        db, fix.match_id, 1, 1, "fifa", SourceTier.OFFICIAL_COMPETITION, "Finished"
                    )
                    await db.commit()

                    consensus = await fix.service.build_consensus(db, fix.match_id)
                    assert consensus is not None
                    assert consensus.is_verified is False
                    assert consensus.source_count == 1
                    assert consensus.source_names == ["FIFA"]
            finally:
                await fix.teardown()
        _run_async(_test())

    def test_two_sources_different_score_conflict(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    await fix.service.add_source_result(
                        db, fix.match_id, 1, 0, "ESPN", SourceTier.REPUTABLE_MEDIA, "FT"
                    )
                    await fix.service.add_source_result(
                        db, fix.match_id, 3, 0, "AFA", SourceTier.OFFICIAL_FEDERATION, "FT"
                    )
                    await db.commit()

                    consensus = await fix.service.build_consensus(db, fix.match_id)
                    assert consensus.is_verified is False  # conflict
                    assert consensus.source_count == 1
            finally:
                await fix.teardown()
        _run_async(_test())

    def test_three_sources_two_agree(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    await fix.service.add_source_result(
                        db, fix.match_id, 3, 0, "AFA", SourceTier.OFFICIAL_FEDERATION, "FT"
                    )
                    await fix.service.add_source_result(
                        db, fix.match_id, 3, 0, "FIFA", SourceTier.OFFICIAL_COMPETITION, "FT"
                    )
                    await fix.service.add_source_result(
                        db, fix.match_id, 1, 0, "ESPN", SourceTier.REPUTABLE_MEDIA, "FT"
                    )
                    await db.commit()

                    consensus = await fix.service.build_consensus(db, fix.match_id)
                    assert consensus.is_verified is True
                    assert consensus.home_goals == 3  # majority
                    assert consensus.source_count == 2
            finally:
                await fix.teardown()
        _run_async(_test())

    def test_no_sources_returns_none(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    consensus = await fix.service.build_consensus(db, fix.match_id)
                    assert consensus is None
            finally:
                await fix.teardown()
        _run_async(_test())

    def test_idempotent_build_consensus(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    await fix.service.add_source_result(
                        db, fix.match_id, 2, 1, "AFA", SourceTier.OFFICIAL_FEDERATION, "FT"
                    )
                    await fix.service.add_source_result(
                        db, fix.match_id, 2, 1, "FIFA", SourceTier.OFFICIAL_COMPETITION, "FT"
                    )
                    await db.commit()

                    c1 = await fix.service.build_consensus(db, fix.match_id)
                    assert c1.is_verified is True
                    c2 = await fix.service.build_consensus(db, fix.match_id)
                    assert c2 is not None
                    assert c2.is_verified is True
                    assert str(c2.verification_id) == str(c1.verification_id)
                    verified, vid2 = await fix.service.is_verified(db, fix.match_id)
                    assert verified is True
                    assert str(vid2) == str(c1.verification_id)
            finally:
                await fix.teardown()
        _run_async(_test())

    # ── is_verified ──────────────────────────────────────────────

    def test_is_verified_after_consensus(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    await fix.service.add_source_result(
                        db, fix.match_id, 4, 0, "AFA", SourceTier.OFFICIAL_FEDERATION, "FT"
                    )
                    await fix.service.add_source_result(
                        db, fix.match_id, 4, 0, "FIFA", SourceTier.OFFICIAL_COMPETITION, "FT"
                    )
                    await db.commit()
                    await fix.service.build_consensus(db, fix.match_id)

                    verified, vid = await fix.service.is_verified(db, fix.match_id)
                    assert verified is True
                    assert vid is not None
            finally:
                await fix.teardown()
        _run_async(_test())

    def test_is_verified_without_consensus(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    await fix.service.add_source_result(
                        db, fix.match_id, 2, 1, "AFA", SourceTier.OFFICIAL_FEDERATION, "FT"
                    )
                    await db.commit()

                    verified, vid = await fix.service.is_verified(db, fix.match_id)
                    assert verified is False
                    assert vid is None
            finally:
                await fix.teardown()
        _run_async(_test())

    # ── get_conflicts ────────────────────────────────────────────

    def test_get_conflicts_two_groups(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    await fix.service.add_source_result(
                        db, fix.match_id, 1, 0, "API", SourceTier.REPUTABLE_DATA_PROVIDER, "FT"
                    )
                    await fix.service.add_source_result(
                        db, fix.match_id, 3, 0, "AFA", SourceTier.OFFICIAL_FEDERATION, "FT"
                    )
                    await db.commit()

                    conflicts = await fix.service.get_conflicts(db, fix.match_id)
                    assert len(conflicts) == 2
            finally:
                await fix.teardown()
        _run_async(_test())

    def test_get_conflicts_no_conflict(self):
        async def _test():
            fix = _IntegrationFixture()
            await fix.setup()
            try:
                async with fix.session() as db:
                    await fix.service.add_source_result(
                        db, fix.match_id, 3, 0, "AFA", SourceTier.OFFICIAL_FEDERATION, "FT"
                    )
                    await db.commit()

                    conflicts = await fix.service.get_conflicts(db, fix.match_id)
                    assert len(conflicts) == 0
            finally:
                await fix.teardown()
        _run_async(_test())
