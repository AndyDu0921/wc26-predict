"""Typed records for V4.11 rich match data.

The schema is intentionally provider-neutral.  FIFA official data is the first
adapter, but the normalized records can also hold StatsBomb/Wyscout/SofaScore
style event streams without changing downstream post-match learning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawOfficialMatchData:
    match_id: str
    provider: str
    source_url: str
    payload: dict[str, Any]
    provider_match_id: str | None = None
    fetched_at: str | None = None
    payload_hash: str | None = None
    content_type: str | None = None
    parser_version: str = "v4.11"
    status: str = "fetched"
    data_scope: str = "postmatch"
    notes: str | None = None


@dataclass
class MatchEvent:
    match_id: str
    provider: str
    minute: int | None
    event_type: str
    team_name: str | None = None
    side: str | None = None
    player_name: str | None = None
    related_player_name: str | None = None
    stoppage_minute: int | None = None
    period: str | None = None
    outcome: str | None = None
    xg: float | None = None
    provider_event_id: str | None = None
    home_score_after: int | None = None
    away_score_after: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ShotEvent:
    match_id: str
    provider: str
    minute: int | None
    team_name: str | None = None
    side: str | None = None
    player_name: str | None = None
    stoppage_minute: int | None = None
    period: str | None = None
    xg: float | None = None
    outcome: str | None = None
    body_part: str | None = None
    shot_type: str | None = None
    assist_player: str | None = None
    event_id: str | None = None
    home_score_after: int | None = None
    away_score_after: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class LineupPlayer:
    match_id: str
    provider: str
    team_name: str | None
    player_name: str
    side: str | None = None
    player_id: str | None = None
    position: str | None = None
    shirt_number: int | None = None
    is_starting: bool | None = None
    is_captain: bool | None = None
    is_goalkeeper: bool | None = None
    minute_on: int | None = None
    minute_off: int | None = None
    minutes_played: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlayerMatchStats:
    match_id: str
    provider: str
    team_name: str | None
    player_name: str
    side: str | None = None
    player_id: str | None = None
    minutes_played: int | None = None
    goals: int | None = None
    assists: int | None = None
    shots: int | None = None
    xg: float | None = None
    passes_attempted: int | None = None
    pass_accuracy_pct: float | None = None
    tackles: int | None = None
    saves: int | None = None
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class GameStateSegment:
    match_id: str
    minute_start: int
    minute_end: int
    provider: str = "derived"
    period: str | None = None
    home_score_start: int = 0
    away_score_start: int = 0
    home_score_end: int = 0
    away_score_end: int = 0
    leader_start: str = "draw"
    leader_end: str = "draw"
    home_events_count: int = 0
    away_events_count: int = 0
    home_shots: int = 0
    away_shots: int = 0
    home_xg: float | None = None
    away_xg: float | None = None
    cards_count: int = 0
    substitutions_count: int = 0
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedMatchData:
    match_id: str
    provider: str
    events: list[MatchEvent] = field(default_factory=list)
    shots: list[ShotEvent] = field(default_factory=list)
    lineups: list[LineupPlayer] = field(default_factory=list)
    player_stats: list[PlayerMatchStats] = field(default_factory=list)
    report_links: list[str] = field(default_factory=list)
    status: str = "partial"
    warnings: list[str] = field(default_factory=list)

