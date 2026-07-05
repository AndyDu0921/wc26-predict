"""ResultVerificationService — verify match scores via independent consensus.

Pattern:
1. Callers add source claims via add_source_result()
2. build_consensus() groups by score and requires 2+ trusted independent sources
3. is_verified() returns whether a verified consensus exists for the match

Usage:
    service = get_verification_service()
    await service.add_source_result(db, match_id, 3, 0, "AFA", 1, "FT")
    consensus = await service.build_consensus(db, match_id)
    if consensus and consensus.is_verified:
        # Pass consensus.verification_id to LearningEngine
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match_result_verification import MatchResultVerification

logger = logging.getLogger(__name__)

# Match statuses considered "finished" for verification purposes
_FINISHED_STATUSES = frozenset({"FT", "Finished", "Final", "full_time", "finished"})

# Source names in this list are useful as audit notes, but they are not
# independent external evidence and must never help verify a result.
_UNTRUSTED_SOURCE_FAMILIES = frozenset(
    {
        "userprovided",
        "userinput",
        "manual",
        "manualinput",
        "manualinject",
        "humanprovided",
        "claudecodewebsearch",
    }
)


# ── source tier constants ──────────────────────────────────────────────

class SourceTier:
    """Semantic labels for source_tier values (1-6)."""
    OFFICIAL_FEDERATION = 1     # AFA, FIFA, UEFA, CONMEBOL
    OFFICIAL_COMPETITION = 2    # World Cup official site, Copa America official
    REPUTABLE_DATA_PROVIDER = 3  # API-Football, Opta, StatsBomb
    REPUTABLE_MEDIA = 4         # ESPN, BBC, Sky Sports
    AGGREGATOR = 5              # FlashScore, LiveScore
    OTHER = 6                   # Claude Code Web Search, user input


def _source_family(source_name: str) -> str:
    """Normalize a source name into a coarse independence family."""
    return re.sub(r"[^a-z0-9]", "", source_name.lower())


def _match_id_key(match_id: str | UUID) -> str:
    """Return the real string key stored in ``matches.id``.

    Historical match rows usually store UUIDs as 32-char hex strings, while
    WC26 schedule-backed rows can use numeric strings such as ``"195"``.
    """
    if isinstance(match_id, UUID):
        return match_id.hex
    text = str(match_id).strip()
    try:
        return UUID(text).hex
    except (TypeError, ValueError):
        return text


def _is_trusted_independent_source(row: MatchResultVerification) -> bool:
    """Return whether a source row can count toward verified consensus."""
    if row.source_tier >= SourceTier.OTHER:
        return False
    return _source_family(row.source_name) not in _UNTRUSTED_SOURCE_FAMILIES


def _independent_trusted_rows(
    rows: list[MatchResultVerification],
) -> list[MatchResultVerification]:
    """Deduplicate trusted source rows by independence family."""
    selected: dict[str, MatchResultVerification] = {}
    for row in sorted(rows, key=lambda r: (r.source_tier, r.source_name)):
        if not _is_trusted_independent_source(row):
            continue
        family = _source_family(row.source_name)
        selected.setdefault(family, row)
    return list(selected.values())


# ── dataclass ───────────────────────────────────────────────────────────

@dataclass
class ConsensusResult:
    """Result of a consensus-building operation."""
    match_id: str
    home_goals: int
    away_goals: int
    source_count: int
    source_names: list[str] = field(default_factory=list)
    is_verified: bool = False
    verification_id: UUID | None = None


# ── service ─────────────────────────────────────────────────────────────

class ResultVerificationService:
    """Multi-source match score verification service.

    All methods are async and operate on an AsyncSession.
    No singleton state needed — the class is stateless.
    """

    # ── public API ──────────────────────────────────────────────────

    @staticmethod
    async def add_source_result(
        db: AsyncSession,
        match_id: str,
        home_goals: int,
        away_goals: int,
        source_name: str,
        source_tier: int,
        match_status: str,
        notes: str | None = None,
    ) -> MatchResultVerification:
        """Record a single source's claim about a match result.

        Args:
            db: Active async database session.
            match_id: ID of the match in the matches table.
            home_goals, away_goals: Score claimed by this source.
            source_name: Human-readable source identifier (e.g. "AFA", "ESPN").
            source_tier: 1–6 per SourceTier constants.
            match_status: Match status as reported ("FT", "Finished", etc.).
            notes: Optional free-form context.

        Returns:
            The created MatchResultVerification row.

        Raises:
            ValueError: If match_status is not a recognised finished status.
        """
        match_key = _match_id_key(match_id)
        normalized_status = match_status.strip()
        if normalized_status not in _FINISHED_STATUSES:
            raise ValueError(
                f"Rejecting source claim for match {match_key}: "
                f"match_status='{match_status}' is not a finished status. "
                f"Accepted: {sorted(_FINISHED_STATUSES)}"
            )

        record = MatchResultVerification(
            match_id=match_key,
            home_goals=home_goals,
            away_goals=away_goals,
            source_name=source_name,
            source_tier=source_tier,
            match_status_at_source=normalized_status,
            is_consensus=False,
            notes=notes,
        )
        db.add(record)
        await db.flush()
        logger.info(
            "Recorded source claim: match=%s source=%s score=%d-%d status=%s",
            match_key, source_name, home_goals, away_goals, normalized_status,
        )
        return record

    @staticmethod
    async def build_consensus(
        db: AsyncSession,
        match_id: str,
    ) -> ConsensusResult | None:
        """Build consensus from all non-consensus source rows for this match.

        Groups source rows by (home_goals, away_goals).  If 2+ trusted,
        independent source families agree on the same score, creates a
        consensus row and links the contributing trusted source rows to it.

        Args:
            db: Active async database session.
            match_id: ID of the match to build consensus for.

        Returns:
            ConsensusResult if at least one source row exists, else None.
            is_verified=True only when 2+ trusted independent sources agree.
        """
        match_key = _match_id_key(match_id)
        # Read all non-consensus source rows for this match
        result = await db.execute(
            select(MatchResultVerification).where(
                and_(
                    MatchResultVerification.match_id == match_key,
                    MatchResultVerification.is_consensus == False,  # noqa: E712
                    MatchResultVerification.consensus_for_id.is_(None),
                )
            )
        )
        rows = list(result.scalars().all())

        if not rows:
            return None

        # Group by score
        groups: dict[tuple[int, int], list[MatchResultVerification]] = {}
        for row in rows:
            key = (row.home_goals, row.away_goals)
            groups.setdefault(key, []).append(row)

        # Find the score group with the most trusted independent evidence.
        # User-provided and other tier-6 rows remain visible in the audit trail
        # but cannot help a result enter the learning loop.
        best_group = max(
            groups.values(),
            key=lambda group: (len(_independent_trusted_rows(group)), len(group)),
        )
        best_score = (best_group[0].home_goals, best_group[0].away_goals)
        trusted_group = _independent_trusted_rows(best_group)
        source_count = len(trusted_group)

        # Verified requires 2+ trusted independent source families
        is_verified = source_count >= 2

        if not is_verified:
            source_names = sorted({r.source_name for r in trusted_group or best_group})
            return ConsensusResult(
                match_id=match_key,
                home_goals=best_score[0],
                away_goals=best_score[1],
                source_count=source_count,
                source_names=source_names,
                is_verified=False,
            )

        # Create consensus row
        source_names = sorted({r.source_name for r in trusted_group})
        consensus = MatchResultVerification(
            match_id=match_key,
            home_goals=best_score[0],
            away_goals=best_score[1],
            source_name="|".join(source_names),
            source_tier=min(r.source_tier for r in trusted_group),
            match_status_at_source="FT",
            is_consensus=True,
            notes=(
                f"Consensus from {source_count} trusted independent sources: "
                f"{', '.join(source_names)}"
            ),
        )
        db.add(consensus)
        await db.flush()

        # Link source rows to the consensus row
        for row in trusted_group:
            row.consensus_for_id = consensus.id

        await db.flush()
        logger.info(
            "Consensus built: match=%s score=%d-%d sources=%d verified=True",
            match_key, best_score[0], best_score[1], source_count,
        )

        return ConsensusResult(
            match_id=match_key,
            home_goals=best_score[0],
            away_goals=best_score[1],
            source_count=source_count,
            source_names=source_names,
            is_verified=True,
            verification_id=consensus.id,
        )

    @staticmethod
    async def is_verified(
        db: AsyncSession,
        match_id: str,
    ) -> tuple[bool, UUID | None]:
        """Check if a verified consensus exists for this match.

        Args:
            db: Active async database session.
            match_id: ID of the match.

        Returns:
            (True, consensus_id) if a verified consensus exists,
            (False, None) otherwise.
        """
        match_key = _match_id_key(match_id)
        result = await db.execute(
            select(MatchResultVerification).where(
                and_(
                    MatchResultVerification.match_id == match_key,
                    MatchResultVerification.is_consensus == True,  # noqa: E712
                )
            )
        )
        consensus_rows = list(result.scalars().all())
        for row in consensus_rows:
            source_result = await db.execute(
                select(MatchResultVerification).where(
                    MatchResultVerification.consensus_for_id == row.id
                )
            )
            linked_sources = list(source_result.scalars().all())
            if len(_independent_trusted_rows(linked_sources)) >= 2:
                return (True, row.id)
        return (False, None)

    @staticmethod
    async def get_conflicts(
        db: AsyncSession,
        match_id: str,
    ) -> list[dict[str, object]]:
        """Return conflicting score claims for a match.

        A conflict exists when multiple score groups have source rows
        and no consensus has been reached yet.
        """
        match_key = _match_id_key(match_id)
        result = await db.execute(
            select(MatchResultVerification).where(
                and_(
                    MatchResultVerification.match_id == match_key,
                    MatchResultVerification.is_consensus == False,  # noqa: E712
                    MatchResultVerification.consensus_for_id.is_(None),
                )
            )
        )
        rows = list(result.scalars().all())

        groups: dict[tuple[int, int], list[MatchResultVerification]] = {}
        for row in rows:
            key = (row.home_goals, row.away_goals)
            groups.setdefault(key, []).append(row)

        if len(groups) <= 1:
            return []

        return [
            {
                "score": f"{home}-{away}",
                "source_count": len(srcs),
                "sources": [s.source_name for s in srcs],
            }
            for (home, away), srcs in groups.items()
        ]


# ── singleton ───────────────────────────────────────────────────────────

_verification_service: ResultVerificationService | None = None


def get_verification_service() -> ResultVerificationService:
    """Return the singleton ResultVerificationService instance."""
    global _verification_service
    if _verification_service is None:
        _verification_service = ResultVerificationService()
    return _verification_service
