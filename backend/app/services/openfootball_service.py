from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.models import Match, MatchResult, Team
from app.models.enums import CompetitionType
from app.models.enums import MatchStatus
from app.models.enums import TeamType
from app.services.team_resolver import TeamResolver
from app.utils.datetime import utc_now
from app.utils.http import fetch_json

logger = get_logger(__name__)


class OpenFootballService:
    base_url = "https://raw.githubusercontent.com/openfootball/worldcup.json/master"

    def __init__(self) -> None:
        self.team_resolver = TeamResolver()

    async def fetch_world_cup_json(self, year: int) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            return await fetch_json(client, f"{self.base_url}/{year}/worldcup.json")

    async def sync_world_cup_year(self, year: int, db: AsyncSession) -> dict[str, int]:
        payload = await self.fetch_world_cup_json(year)
        matches = self._extract_matches(payload)
        created_matches = 0
        updated_results = 0
        touched_teams = 0

        for index, match_payload in enumerate(matches, start=1):
            home_team, home_created = await self._upsert_team(self._team_ref(match_payload, "team1"), db)
            away_team, away_created = await self._upsert_team(self._team_ref(match_payload, "team2"), db)
            touched_teams += int(home_created) + int(away_created)

            external_id = self._build_external_id(year, index, home_team.name, away_team.name, match_payload)
            match_date = self._parse_match_date(match_payload)
            result = await db.execute(select(Match).where(Match.external_id == external_id))
            match = result.scalar_one_or_none()
            created = match is None

            if match is None:
                match = Match(
                    external_id=external_id,
                    home_team_id=home_team.id,
                    away_team_id=away_team.id,
                    match_date=match_date,
                    competition=payload.get("name") or f"FIFA World Cup {year}",
                    competition_type=CompetitionType.NATIONAL,
                    competition_weight=1.0,
                    stage=self._match_stage(match_payload),
                    venue=self._match_venue(match_payload),
                    is_neutral_venue=True,
                    status=self._infer_status(match_payload, match_date),
                )
                db.add(match)
                await db.flush()
            else:
                match.home_team_id = home_team.id
                match.away_team_id = away_team.id
                match.match_date = match_date
                match.competition = payload.get("name") or match.competition
                match.competition_type = CompetitionType.NATIONAL
                match.stage = self._match_stage(match_payload)
                match.venue = self._match_venue(match_payload)
                match.is_neutral_venue = True
                match.competition_weight = 1.0
                match.status = self._infer_status(match_payload, match_date)

            if created:
                created_matches += 1

            if self._has_score(match_payload):
                updated_results += int(await self._upsert_match_result(match.id, match_payload, db))

        await db.commit()
        logger.info(
            "OpenFootball sync finished for %s: matches=%s created=%s result_updates=%s team_touches=%s",
            year,
            len(matches),
            created_matches,
            updated_results,
            touched_teams,
        )
        return {
            "source_matches": len(matches),
            "created_matches": created_matches,
            "updated_results": updated_results,
            "touched_teams": touched_teams,
        }

    def _extract_matches(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        if isinstance(payload.get("matches"), list):
            return [item for item in payload["matches"] if isinstance(item, dict)]

        rounds = payload.get("rounds") or []
        matches: list[dict[str, Any]] = []
        for round_payload in rounds:
            if not isinstance(round_payload, dict):
                continue
            round_name = round_payload.get("name")
            for match in round_payload.get("matches", []):
                if not isinstance(match, dict):
                    continue
                merged = dict(match)
                if round_name and "round" not in merged:
                    merged["round"] = round_name
                matches.append(merged)
        return matches

    async def _upsert_team(self, team_payload: Mapping[str, Any] | str, db: AsyncSession) -> tuple[Team, bool]:
        if isinstance(team_payload, str):
            team_name = team_payload
            fifa_code = self._extract_trailing_code(team_payload)
        else:
            team_name = str(team_payload.get("name") or "Unknown")
            fifa_code = team_payload.get("code") or self._extract_trailing_code(team_name)

        canonical_name = self._clean_team_name(team_name)
        team = await self.team_resolver.resolve_team(str(fifa_code or canonical_name), db)
        if team is None:
            team = await self.team_resolver.resolve_team(canonical_name, db)

        created = team is None
        if team is None:
            team = Team(
                name=canonical_name,
                name_zh=canonical_name,
                fifa_code=(str(fifa_code)[:3].upper() if fifa_code else None),
                team_type=TeamType.NATIONAL,
                confederation="FIFA",
                elo_rating=1500.0,
            )
            db.add(team)
            await db.flush()
        else:
            team.team_type = TeamType.NATIONAL
            if not team.fifa_code and fifa_code:
                team.fifa_code = str(fifa_code)[:3].upper()
            if not team.confederation:
                team.confederation = "FIFA"

        await self.team_resolver.ensure_aliases(
            team,
            [canonical_name, team_name, str(fifa_code or "")],
            db,
            source="openfootball",
        )
        return team, created

    async def _upsert_match_result(self, match_id, payload: Mapping[str, Any], db: AsyncSession) -> bool:
        home_goals, away_goals = self._score_tuple(payload)
        result = await db.execute(select(MatchResult).where(MatchResult.match_id == match_id))
        match_result = result.scalar_one_or_none()
        created = match_result is None
        if match_result is None:
            db.add(
                MatchResult(
                    match_id=match_id,
                    home_goals=home_goals,
                    away_goals=away_goals,
                    home_xg=None,
                    away_xg=None,
                )
            )
        else:
            match_result.home_goals = home_goals
            match_result.away_goals = away_goals
        return created

    def _build_external_id(
        self,
        year: int,
        index: int,
        home_team_name: str,
        away_team_name: str,
        payload: Mapping[str, Any],
    ) -> str:
        match_number = payload.get("num") or index
        return f"openfootball:wc:{year}:{int(match_number):03d}"

    def _parse_match_date(self, payload: Mapping[str, Any]) -> datetime:
        raw_date = str(payload.get("date") or utc_now().date().isoformat())
        raw_time = str(payload.get("time") or "18:00")
        raw_time = raw_time.replace(".", ":")
        if len(raw_time) == 5:
            raw_time = f"{raw_time}:00"
        candidate = f"{raw_date}T{raw_time}"
        try:
            return datetime.fromisoformat(candidate).replace(tzinfo=UTC)
        except ValueError:
            return utc_now()

    def _match_stage(self, payload: Mapping[str, Any]) -> str | None:
        return str(payload.get("group") or payload.get("round") or payload.get("stage") or "")[:50] or None

    def _match_venue(self, payload: Mapping[str, Any]) -> str | None:
        venue = payload.get("ground")
        if isinstance(venue, Mapping):
            return str(venue.get("name") or "")[:100] or None
        stadium = payload.get("stadium")
        if isinstance(stadium, Mapping):
            return str(stadium.get("name") or "")[:100] or None
        return str(venue or "")[:100] or None

    def _infer_status(self, payload: Mapping[str, Any], match_date: datetime) -> MatchStatus:
        if self._has_score(payload):
            return MatchStatus.FINISHED
        if match_date < utc_now():
            return MatchStatus.SCHEDULED
        return MatchStatus.SCHEDULED

    def _has_score(self, payload: Mapping[str, Any]) -> bool:
        home_goals, away_goals = self._score_tuple(payload)
        return home_goals is not None and away_goals is not None

    def _score_tuple(self, payload: Mapping[str, Any]) -> tuple[int | None, int | None]:
        if isinstance(payload.get("score"), Mapping):
            score = payload["score"]
            for key in ("ft", "et", "p"):
                value = score.get(key)
                if isinstance(value, list) and len(value) == 2:
                    return int(value[0]), int(value[1])
        if payload.get("score1") is not None and payload.get("score2") is not None:
            return int(payload["score1"]), int(payload["score2"])
        return None, None

    def _team_ref(self, payload: Mapping[str, Any], key: str) -> Mapping[str, Any] | str:
        value = payload.get(key)
        return value if isinstance(value, (dict, str)) else "Unknown"

    @staticmethod
    def _clean_team_name(value: str) -> str:
        return value.rsplit("(", 1)[0].strip() if value.endswith(")") and "(" in value else value.strip()

    @staticmethod
    def _extract_trailing_code(value: str) -> str | None:
        if value.endswith(")") and "(" in value:
            return value.rsplit("(", 1)[1].rstrip(")").strip()
        return None
