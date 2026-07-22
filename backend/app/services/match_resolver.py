"""Match resolver for binding predictions and external data to matches.

The resolver is deliberately conservative: it returns a match only when the
team pair is exact after normalization and either the kickoff time is close or
the team-pair/competition combination is unique.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.sqlite_paths import current_sync_sqlite_path


_ALIASES = {
    "usa": "united states",
    "usmnt": "united states",
    "u s a": "united states",
    "south korea republic of korea": "south korea",
    "korea republic": "south korea",
    "cote divoire": "ivory coast",
    "côte divoire": "ivory coast",
    "england fc": "england",
}

_CLUB_SUFFIXES = (" football club", " fc", " afc", " cf", " sc")


@dataclass(frozen=True)
class ResolvedMatch:
    match_id: str
    confidence: float
    reason: str
    home_team: str
    away_team: str
    competition: str
    match_date: str
    stage: str | None = None


def normalize_name(value: str | None) -> str:
    """Normalize team/competition labels for matching."""
    text = (value or "").strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"['’`]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = _ALIASES.get(text, text)
    for suffix in _CLUB_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    return _ALIASES.get(text, text)


def normalize_uuid(value: str | None) -> str | None:
    """Return a compact 32-char UUID if possible."""
    clean = str(value or "").replace("-", "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{32}", clean):
        return clean
    return None


def is_uuid_like(value: str | None) -> bool:
    return normalize_uuid(value) is not None


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00").replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text.split(".")[0])
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _competition_matches(requested: str, stored: str) -> bool:
    req = normalize_name(requested)
    got = normalize_name(stored)
    if not req or not got:
        return True
    return req == got or req in got or got in req


def resolve_match_id(
    *,
    home_team: str,
    away_team: str,
    competition: str = "",
    kickoff_at: str | None = None,
    stage: str | None = None,
    db_path: str | Path | None = None,
    min_confidence: float = 0.82,
) -> ResolvedMatch | None:
    """Resolve a match id from team names, competition, and optional time."""
    path = (
        Path(db_path).expanduser().resolve()
        if db_path is not None
        else current_sync_sqlite_path()
    )
    if not path.exists():
        return None

    home_norm = normalize_name(home_team)
    away_norm = normalize_name(away_team)
    if not home_norm or not away_norm:
        return None

    kickoff = _parse_time(kickoff_at)
    stage_norm = normalize_name(stage)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = list(
            conn.execute(
                """
                SELECT
                    m.id,
                    m.match_date,
                    m.competition,
                    m.stage,
                    ht.name AS home_team,
                    at.name AS away_team
                FROM matches m
                JOIN teams ht ON ht.id = m.home_team_id
                JOIN teams at ON at.id = m.away_team_id
                """
            )
        )
    finally:
        conn.close()

    candidates: list[tuple[float, str, sqlite3.Row]] = []
    for row in rows:
        if normalize_name(row["home_team"]) != home_norm:
            continue
        if normalize_name(row["away_team"]) != away_norm:
            continue
        if not _competition_matches(competition, row["competition"]):
            continue

        score = 0.72
        reasons = ["team_pair"]

        if competition and _competition_matches(competition, row["competition"]):
            score += 0.10
            reasons.append("competition")

        match_dt = _parse_time(row["match_date"])
        if kickoff and match_dt:
            delta_hours = abs((match_dt - kickoff).total_seconds()) / 3600
            if delta_hours <= 3:
                score += 0.20
                reasons.append("time<=3h")
            elif delta_hours <= 36:
                score += 0.12
                reasons.append("time<=36h")
            else:
                continue

        if stage_norm and stage_norm in normalize_name(row["stage"]):
            score += 0.05
            reasons.append("stage")

        candidates.append((min(score, 1.0), "+".join(reasons), row))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score, reason, best = candidates[0]

    if kickoff is None and len(candidates) > 1:
        # Without time, only bind if there is exactly one team-pair/competition match.
        return None
    if len(candidates) > 1 and best_score - candidates[1][0] < 0.08:
        return None
    if best_score < min_confidence:
        return None

    match_id = normalize_uuid(best["id"])
    if match_id is None:
        return None
    return ResolvedMatch(
        match_id=match_id,
        confidence=round(best_score, 4),
        reason=reason,
        home_team=str(best["home_team"]),
        away_team=str(best["away_team"]),
        competition=str(best["competition"]),
        match_date=str(best["match_date"]),
        stage=str(best["stage"] or ""),
    )


def resolve_match_id_from_mapping(
    data: dict[str, Any],
    *,
    db_path: str | Path | None = None,
    min_confidence: float = 0.82,
) -> ResolvedMatch | None:
    """Resolve from a dict-like row with common prediction snapshot keys."""
    return resolve_match_id(
        home_team=str(data.get("home_team") or data.get("home_team_name") or ""),
        away_team=str(data.get("away_team") or data.get("away_team_name") or ""),
        competition=str(data.get("competition") or ""),
        kickoff_at=str(data.get("kickoff_at") or data.get("match_time") or data.get("match_date") or ""),
        stage=str(data.get("stage") or ""),
        db_path=db_path,
        min_confidence=min_confidence,
    )
