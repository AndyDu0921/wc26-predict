"""Normalize rich match data payloads into provider-neutral records."""

from __future__ import annotations

import re
from typing import Any

from app.services.match_data.schema import (
    LineupPlayer,
    MatchEvent,
    NormalizedMatchData,
    PlayerMatchStats,
    ShotEvent,
)


EVENT_CONTAINER_KEYS = {
    "events",
    "timeline",
    "timelines",
    "match_events",
    "matchEvents",
    "incidents",
    "actions",
}
SHOT_CONTAINER_KEYS = {"shots", "shot_events", "shotEvents", "attempts"}
LINEUP_CONTAINER_KEYS = {"lineups", "lineup", "starting_lineups", "startingLineups"}
PLAYER_STATS_CONTAINER_KEYS = {
    "player_statistics",
    "playerStats",
    "player_statistics_rows",
    "players",
}

GOAL_TYPES = {"goal", "own_goal", "penalty_goal"}
SHOT_TYPES = {"shot", "goal", "penalty_goal", "penalty_saved", "penalty_missed", "own_goal"}


def normalize_official_payload(
    payload: dict[str, Any],
    *,
    match_id: str,
    provider: str,
    home_team: str | None = None,
    away_team: str | None = None,
) -> NormalizedMatchData:
    """Normalize a raw official/provider payload.

    This function is intentionally schema-tolerant.  It supports the compact
    fixture shape used in tests and common event-provider aliases.  Unknown
    fields are kept inside payload_json rather than invented.
    """
    warnings: list[str] = []
    fifa_live = _normalize_fifa_live_payloads(
        payload,
        match_id=match_id,
        provider=provider,
        home_team=home_team,
        away_team=away_team,
    )
    events = [
        event
        for item in _candidate_records(payload, EVENT_CONTAINER_KEYS)
        if (event := _normalize_event(item, match_id, provider, home_team, away_team)) is not None
    ]
    events.extend(fifa_live["events"])
    explicit_shots = [
        shot
        for item in _candidate_records(payload, SHOT_CONTAINER_KEYS)
        if (shot := _normalize_shot(item, match_id, provider, home_team, away_team)) is not None
    ]
    event_shots = [
        _shot_from_event(event)
        for event in events
        if event.event_type in SHOT_TYPES
    ]
    shots = explicit_shots + [shot for shot in event_shots if shot is not None]
    lineups = _normalize_lineups(payload, match_id, provider, home_team, away_team)
    lineups.extend(fifa_live["lineups"])
    player_stats = [
        stats
        for item in _candidate_records(payload, PLAYER_STATS_CONTAINER_KEYS)
        if (stats := _normalize_player_stats(item, match_id, provider, home_team, away_team)) is not None
    ]
    player_stats.extend(fifa_live["player_stats"])
    report_links = _extract_report_links(payload)
    warnings.extend(fifa_live["warnings"])

    if not events:
        warnings.append("no_event_timeline_found")
    if not lineups:
        warnings.append("no_lineups_found")
    if not player_stats:
        warnings.append("no_player_statistics_found")
    if not shots:
        warnings.append("no_shot_events_found")
    elif not explicit_shots and event_shots:
        warnings.append("shot_events_from_goal_events_only")

    status = "parsed" if events or lineups or player_stats or shots else "partial"
    return NormalizedMatchData(
        match_id=str(match_id),
        provider=provider,
        events=events,
        shots=shots,
        lineups=lineups,
        player_stats=player_stats,
        report_links=report_links,
        status=status,
        warnings=warnings,
    )


def _normalize_fifa_live_payloads(
    payload: dict[str, Any],
    *,
    match_id: str,
    provider: str,
    home_team: str | None,
    away_team: str | None,
) -> dict[str, list[Any]]:
    events: list[MatchEvent] = []
    lineups: list[LineupPlayer] = []
    player_stats: list[PlayerMatchStats] = []
    warnings: list[str] = []
    for item in _fifa_live_payload_candidates(payload):
        normalized = _normalize_fifa_live_payload(
            item,
            match_id=match_id,
            provider=provider,
            home_team=home_team,
            away_team=away_team,
        )
        events.extend(normalized["events"])
        lineups.extend(normalized["lineups"])
        player_stats.extend(normalized["player_stats"])
        warnings.extend(normalized["warnings"])
    return {
        "events": events,
        "lineups": lineups,
        "player_stats": player_stats,
        "warnings": warnings,
    }


def _fifa_live_payload_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if isinstance(payload.get("HomeTeam"), dict) and isinstance(payload.get("AwayTeam"), dict):
        candidates.append(payload)
    for item in payload.get("structured_payloads") or []:
        if isinstance(item, dict) and isinstance(item.get("HomeTeam"), dict) and isinstance(item.get("AwayTeam"), dict):
            candidates.append(item)
    return candidates


def _normalize_fifa_live_payload(
    payload: dict[str, Any],
    *,
    match_id: str,
    provider: str,
    home_team: str | None,
    away_team: str | None,
) -> dict[str, list[Any]]:
    home = payload.get("HomeTeam") or {}
    away = payload.get("AwayTeam") or {}
    home_team_name = home_team or _localized_name(home.get("TeamName"))
    away_team_name = away_team or _localized_name(away.get("TeamName"))
    player_lookup = {
        **_fifa_player_lookup(home),
        **_fifa_player_lookup(away),
    }
    sub_index = _fifa_substitution_index(home, away)
    events: list[MatchEvent] = []
    lineups: list[LineupPlayer] = []
    for side, team, team_name in (
        ("home", home, home_team_name),
        ("away", away, away_team_name),
    ):
        events.extend(_fifa_goal_events(team, side, team_name, player_lookup, match_id, provider))
        events.extend(_fifa_booking_events(team, side, team_name, player_lookup, match_id, provider))
        events.extend(_fifa_substitution_events(team, side, team_name, player_lookup, match_id, provider))
        lineups.extend(_fifa_lineups(team, side, team_name, sub_index, match_id, provider))
    player_stats = _fifa_event_derived_player_stats(
        payload,
        match_id=match_id,
        provider=provider,
        home_team=home_team_name,
        away_team=away_team_name,
    )
    warnings = [
        "fifa_live_payload_no_shot_map_xg",
        "fifa_live_player_stats_event_derived_only",
    ]
    return {"events": events, "lineups": lineups, "player_stats": player_stats, "warnings": warnings}


def _fifa_goal_events(
    team: dict[str, Any],
    side: str,
    team_name: str | None,
    player_lookup: dict[str, str],
    match_id: str,
    provider: str,
) -> list[MatchEvent]:
    events = []
    for raw in team.get("Goals") or []:
        minute, stoppage = _parse_minute(raw.get("Minute"))
        player_id = _string_or_none(raw.get("IdPlayer"))
        assist_id = _string_or_none(raw.get("IdAssistPlayer"))
        events.append(
            MatchEvent(
                match_id=str(match_id),
                provider=provider,
                provider_event_id=_string_or_none(raw.get("IdGoal") or raw.get("IdEvent")),
                minute=minute,
                stoppage_minute=stoppage,
                period=_fifa_period_label(raw.get("Period")),
                team_name=team_name,
                side=side,
                player_name=player_lookup.get(player_id or ""),
                related_player_name=player_lookup.get(assist_id or ""),
                event_type=_fifa_goal_type(raw.get("Type")),
                payload=raw,
            )
        )
    return events


def _fifa_booking_events(
    team: dict[str, Any],
    side: str,
    team_name: str | None,
    player_lookup: dict[str, str],
    match_id: str,
    provider: str,
) -> list[MatchEvent]:
    events = []
    for raw in team.get("Bookings") or []:
        minute, stoppage = _parse_minute(raw.get("Minute"))
        player_id = _string_or_none(raw.get("IdPlayer"))
        events.append(
            MatchEvent(
                match_id=str(match_id),
                provider=provider,
                provider_event_id=_string_or_none(raw.get("IdEvent") or raw.get("EventNumber")),
                minute=minute,
                stoppage_minute=stoppage,
                period=_fifa_period_label(raw.get("Period")),
                team_name=team_name,
                side=side,
                player_name=player_lookup.get(player_id or ""),
                event_type=_fifa_card_type(raw.get("Card")),
                payload=raw,
            )
        )
    return events


def _fifa_substitution_events(
    team: dict[str, Any],
    side: str,
    team_name: str | None,
    player_lookup: dict[str, str],
    match_id: str,
    provider: str,
) -> list[MatchEvent]:
    events = []
    for raw in team.get("Substitutions") or []:
        minute, stoppage = _parse_minute(raw.get("Minute"))
        off_id = _string_or_none(raw.get("IdPlayerOff"))
        on_id = _string_or_none(raw.get("IdPlayerOn"))
        events.append(
            MatchEvent(
                match_id=str(match_id),
                provider=provider,
                provider_event_id=_string_or_none(raw.get("IdEvent")),
                minute=minute,
                stoppage_minute=stoppage,
                period=_fifa_period_label(raw.get("Period")),
                team_name=team_name,
                side=side,
                player_name=player_lookup.get(off_id or "") or _localized_name(raw.get("PlayerOffName")),
                related_player_name=player_lookup.get(on_id or "") or _localized_name(raw.get("PlayerOnName")),
                event_type="substitution",
                payload=raw,
            )
        )
    return events


def _fifa_lineups(
    team: dict[str, Any],
    side: str,
    team_name: str | None,
    sub_index: dict[str, dict[str, int | None]],
    match_id: str,
    provider: str,
) -> list[LineupPlayer]:
    records = []
    for raw in team.get("Players") or []:
        player_id = _string_or_none(raw.get("IdPlayer"))
        is_starting = raw.get("Status") == 1
        sub = sub_index.get(player_id or "", {})
        minute_on = 0 if is_starting else sub.get("on")
        minute_off = sub.get("off")
        minutes_played = None
        if minute_on is not None and minute_off is not None and minute_off >= minute_on:
            minutes_played = minute_off - minute_on
        elif is_starting and minute_off is not None:
            minutes_played = minute_off
        records.append(
            LineupPlayer(
                match_id=str(match_id),
                provider=provider,
                team_name=team_name,
                side=side,
                player_id=player_id,
                player_name=_localized_name(raw.get("PlayerName")) or _localized_name(raw.get("ShortName")) or "",
                position=_fifa_position_label(raw.get("Position")),
                shirt_number=_int_or_none(raw.get("ShirtNumber")),
                is_starting=is_starting,
                is_captain=_bool_or_none(raw.get("Captain")),
                is_goalkeeper=raw.get("Position") == 0,
                minute_on=minute_on,
                minute_off=minute_off,
                minutes_played=minutes_played,
                payload=raw,
            )
        )
    return [record for record in records if record.player_name]


def _fifa_event_derived_player_stats(
    payload: dict[str, Any],
    *,
    match_id: str,
    provider: str,
    home_team: str | None,
    away_team: str | None,
) -> list[PlayerMatchStats]:
    stats: dict[tuple[str, str], dict[str, Any]] = {}
    for side, team, team_name in (
        ("home", payload.get("HomeTeam") or {}, home_team),
        ("away", payload.get("AwayTeam") or {}, away_team),
    ):
        player_lookup = _fifa_player_lookup(team)
        for raw in team.get("Goals") or []:
            player_id = _string_or_none(raw.get("IdPlayer"))
            player_name = player_lookup.get(player_id or "")
            if not player_name:
                continue
            key = (side, player_id or player_name)
            row = stats.setdefault(
                key,
                {
                    "side": side,
                    "team_name": team_name,
                    "player_id": player_id,
                    "player_name": player_name,
                    "goals": 0,
                    "assists": 0,
                    "source": "fifa_live_events",
                    "available_fields": ["goals", "assists"],
                },
            )
            row["goals"] += 1
            assist_id = _string_or_none(raw.get("IdAssistPlayer"))
            if assist_id and assist_id in player_lookup:
                assist_key = (side, assist_id)
                assist_row = stats.setdefault(
                    assist_key,
                    {
                        "side": side,
                        "team_name": team_name,
                        "player_id": assist_id,
                        "player_name": player_lookup[assist_id],
                        "goals": 0,
                        "assists": 0,
                        "source": "fifa_live_events",
                        "available_fields": ["goals", "assists"],
                    },
                )
                assist_row["assists"] += 1
    return [
        PlayerMatchStats(
            match_id=str(match_id),
            provider=provider,
            team_name=row["team_name"],
            side=row["side"],
            player_id=row["player_id"],
            player_name=row["player_name"],
            goals=row["goals"],
            assists=row["assists"],
            shots=None,
            xg=None,
            stats=row,
        )
        for row in stats.values()
    ]


def _fifa_player_lookup(team: dict[str, Any]) -> dict[str, str]:
    result = {}
    for player in team.get("Players") or []:
        player_id = _string_or_none(player.get("IdPlayer"))
        name = _localized_name(player.get("PlayerName")) or _localized_name(player.get("ShortName"))
        if player_id and name:
            result[player_id] = name
    return result


def _fifa_substitution_index(*teams: dict[str, Any]) -> dict[str, dict[str, int | None]]:
    index: dict[str, dict[str, int | None]] = {}
    for team in teams:
        for sub in team.get("Substitutions") or []:
            minute, _ = _parse_minute(sub.get("Minute"))
            off_id = _string_or_none(sub.get("IdPlayerOff"))
            on_id = _string_or_none(sub.get("IdPlayerOn"))
            if off_id:
                index.setdefault(off_id, {})["off"] = minute
            if on_id:
                index.setdefault(on_id, {})["on"] = minute
    return index


def _candidate_records(payload: Any, container_keys: set[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def visit(value: Any, key_hint: str | None = None) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in container_keys and isinstance(child, list):
                    records.extend([item for item in child if isinstance(item, dict)])
                elif key in container_keys and isinstance(child, dict):
                    for nested in child.values():
                        if isinstance(nested, list):
                            records.extend([item for item in nested if isinstance(item, dict)])
                        elif isinstance(nested, dict):
                            records.append(nested)
                elif key_hint is None or key in {"data", "match", "home", "away", "HomeTeam", "AwayTeam"}:
                    visit(child, key)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    visit(item, key_hint)

    visit(payload)
    return records


def _normalize_event(
    raw: dict[str, Any],
    match_id: str,
    provider: str,
    home_team: str | None,
    away_team: str | None,
) -> MatchEvent | None:
    event_type = _normalize_event_type(_first(raw, "event_type", "type", "kind", "name", "incidentType"))
    minute, stoppage = _parse_minute(_first(raw, "minute", "matchMinute", "time", "displayMinute"))
    player = _name_from_value(_first(raw, "player", "player_name", "athlete", "scorer"))
    related = _name_from_value(_first(raw, "related_player", "assist", "assistPlayer", "substitute"))
    team = _name_from_value(_first(raw, "team", "team_name", "Team", "contestantName"))
    side = _side_for_team(team, home_team, away_team) or _normalize_side(_first(raw, "side", "homeAway"))
    if team is None:
        team = _team_for_side(side, home_team, away_team)
    if event_type == "unknown" and minute is None and player is None:
        return None
    return MatchEvent(
        match_id=str(match_id),
        provider=provider,
        provider_event_id=_string_or_none(_first(raw, "id", "event_id", "eventId")),
        minute=minute,
        stoppage_minute=stoppage,
        period=_string_or_none(_first(raw, "period", "phase", "half")),
        team_name=team,
        side=side,
        player_name=player,
        related_player_name=related,
        event_type=event_type,
        outcome=_string_or_none(_first(raw, "outcome", "result")),
        xg=_float_or_none(_first(raw, "xg", "expected_goals", "expectedGoals")),
        home_score_after=_int_or_none(_first(raw, "home_score_after", "homeScoreAfter")),
        away_score_after=_int_or_none(_first(raw, "away_score_after", "awayScoreAfter")),
        payload=raw,
    )


def _normalize_shot(
    raw: dict[str, Any],
    match_id: str,
    provider: str,
    home_team: str | None,
    away_team: str | None,
) -> ShotEvent | None:
    minute, stoppage = _parse_minute(_first(raw, "minute", "matchMinute", "time", "displayMinute"))
    player = _name_from_value(_first(raw, "player", "player_name", "shooter", "athlete"))
    team = _name_from_value(_first(raw, "team", "team_name", "Team", "contestantName"))
    side = _side_for_team(team, home_team, away_team) or _normalize_side(_first(raw, "side", "homeAway"))
    if team is None:
        team = _team_for_side(side, home_team, away_team)
    if minute is None and player is None:
        return None
    return ShotEvent(
        match_id=str(match_id),
        provider=provider,
        event_id=_string_or_none(_first(raw, "event_id", "eventId", "id")),
        minute=minute,
        stoppage_minute=stoppage,
        period=_string_or_none(_first(raw, "period", "phase", "half")),
        team_name=team,
        side=side,
        player_name=player,
        xg=_float_or_none(_first(raw, "xg", "expected_goals", "expectedGoals")),
        outcome=_string_or_none(_first(raw, "outcome", "result")),
        body_part=_string_or_none(_first(raw, "body_part", "bodyPart")),
        shot_type=_string_or_none(_first(raw, "shot_type", "type", "situation")),
        assist_player=_name_from_value(_first(raw, "assist", "assistPlayer")),
        home_score_after=_int_or_none(_first(raw, "home_score_after", "homeScoreAfter")),
        away_score_after=_int_or_none(_first(raw, "away_score_after", "awayScoreAfter")),
        payload=raw,
    )


def _shot_from_event(event: MatchEvent) -> ShotEvent | None:
    return ShotEvent(
        match_id=event.match_id,
        provider=event.provider,
        event_id=event.provider_event_id,
        minute=event.minute,
        stoppage_minute=event.stoppage_minute,
        period=event.period,
        team_name=event.team_name,
        side=event.side,
        player_name=event.player_name,
        xg=event.xg,
        outcome=event.outcome or event.event_type,
        home_score_after=event.home_score_after,
        away_score_after=event.away_score_after,
        payload=event.payload,
    )


def _normalize_lineups(
    payload: dict[str, Any],
    match_id: str,
    provider: str,
    home_team: str | None,
    away_team: str | None,
) -> list[LineupPlayer]:
    records: list[LineupPlayer] = []
    for key in LINEUP_CONTAINER_KEYS:
        value = payload.get(key)
        if isinstance(value, dict):
            for side_key, players in value.items():
                side = _normalize_side(side_key)
                team_name = _team_for_side(side, home_team, away_team)
                if isinstance(players, dict):
                    team_name = _name_from_value(players.get("team")) or team_name
                    players = players.get("players") or players.get("lineup") or []
                if isinstance(players, list):
                    records.extend(
                        player
                        for item in players
                        if isinstance(item, dict)
                        if (player := _normalize_lineup_player(
                            item,
                            match_id,
                            provider,
                            team_name,
                            side,
                        )) is not None
                    )
        elif isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                player = _normalize_lineup_player(
                    item,
                    match_id,
                    provider,
                    _name_from_value(_first(item, "team", "team_name")),
                    _normalize_side(_first(item, "side", "homeAway")),
                )
                if player:
                    records.append(player)
    return records


def _normalize_lineup_player(
    raw: dict[str, Any],
    match_id: str,
    provider: str,
    team_name: str | None,
    side: str | None,
) -> LineupPlayer | None:
    player_name = _name_from_value(_first(raw, "player", "player_name", "name", "displayName"))
    if not player_name:
        return None
    minute_on = _int_or_none(_first(raw, "minute_on", "on", "subbedOn"))
    minute_off = _int_or_none(_first(raw, "minute_off", "off", "subbedOff"))
    minutes_played = _int_or_none(_first(raw, "minutes_played", "minutes", "mins"))
    return LineupPlayer(
        match_id=str(match_id),
        provider=provider,
        team_name=team_name,
        side=side,
        player_id=_string_or_none(_first(raw, "player_id", "playerId", "id")),
        player_name=player_name,
        position=_string_or_none(_first(raw, "position", "pos")),
        shirt_number=_int_or_none(_first(raw, "shirt_number", "number", "jerseyNumber")),
        is_starting=_bool_or_none(_first(raw, "is_starting", "starter", "starting")),
        is_captain=_bool_or_none(_first(raw, "is_captain", "captain")),
        is_goalkeeper=_bool_or_none(_first(raw, "is_goalkeeper", "goalkeeper")),
        minute_on=minute_on,
        minute_off=minute_off,
        minutes_played=minutes_played,
        payload=raw,
    )


def _normalize_player_stats(
    raw: dict[str, Any],
    match_id: str,
    provider: str,
    home_team: str | None,
    away_team: str | None,
) -> PlayerMatchStats | None:
    player_name = _name_from_value(_first(raw, "player", "player_name", "name", "displayName"))
    if not player_name:
        return None
    team = _name_from_value(_first(raw, "team", "team_name"))
    side = _side_for_team(team, home_team, away_team) or _normalize_side(_first(raw, "side", "homeAway"))
    return PlayerMatchStats(
        match_id=str(match_id),
        provider=provider,
        team_name=team,
        side=side,
        player_id=_string_or_none(_first(raw, "player_id", "playerId", "id")),
        player_name=player_name,
        minutes_played=_int_or_none(_first(raw, "minutes_played", "minutes", "mins")),
        goals=_int_or_none(_first(raw, "goals", "Gls")),
        assists=_int_or_none(_first(raw, "assists", "Ast")),
        shots=_int_or_none(_first(raw, "shots", "Sh")),
        xg=_float_or_none(_first(raw, "xg", "expected_goals", "expectedGoals")),
        passes_attempted=_int_or_none(_first(raw, "passes_attempted", "passes", "totalPasses")),
        pass_accuracy_pct=_float_or_none(_first(raw, "pass_accuracy_pct", "passAccuracy")),
        tackles=_int_or_none(_first(raw, "tackles", "Tkl")),
        saves=_int_or_none(_first(raw, "saves", "Saves")),
        stats=raw,
    )


def _normalize_event_type(raw_type: Any) -> str:
    text = str(raw_type or "").strip().lower().replace(" ", "_").replace("-", "_")
    if not text:
        return "unknown"
    if "own" in text and "goal" in text:
        return "own_goal"
    if "penalty" in text and "saved" in text:
        return "penalty_saved"
    if "penalty" in text and "miss" in text:
        return "penalty_missed"
    if "penalty" in text and "goal" in text:
        return "penalty_goal"
    if "goal" in text:
        return "goal"
    if "yellow" in text:
        return "yellow_card"
    if "red" in text:
        return "red_card"
    if "sub" in text:
        return "substitution"
    if "var" in text:
        return "var"
    if "shot" in text or "attempt" in text:
        return "shot"
    return text


def _fifa_goal_type(raw_type: Any) -> str:
    # FIFA live endpoint uses numeric goal types, but the public payload does
    # not expose a stable legend. Unknown numeric values remain goal events
    # because the record is already under HomeTeam/AwayTeam.Goals.
    text = str(raw_type or "").lower()
    if "own" in text:
        return "own_goal"
    return "goal"


def _fifa_card_type(raw_card: Any) -> str:
    if str(raw_card) == "1":
        return "yellow_card"
    if str(raw_card) == "2":
        return "red_card"
    return "booking"


def _fifa_period_label(raw_period: Any) -> str | None:
    mapping = {
        "3": "first_half",
        "4": "half_time",
        "5": "second_half",
        "6": "extra_time_first_half",
        "7": "extra_time_second_half",
        "10": "full_time",
    }
    if raw_period is None:
        return None
    return mapping.get(str(raw_period), str(raw_period))


def _fifa_position_label(raw_position: Any) -> str | None:
    mapping = {
        "0": "GK",
        "1": "DF",
        "2": "MF",
        "3": "FW",
    }
    if raw_position is None:
        return None
    return mapping.get(str(raw_position), str(raw_position))


def _parse_minute(raw: Any) -> tuple[int | None, int | None]:
    if raw is None:
        return None, None
    if isinstance(raw, (int, float)):
        return int(raw), None
    text = str(raw).replace("’", "'")
    match = re.search(r"(\d+)(?:\s*'?\s*\+\s*(\d+))?", text)
    if not match:
        return None, None
    minute = int(match.group(1))
    stoppage = int(match.group(2)) if match.group(2) else None
    return minute, stoppage


def _first(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in raw:
            return raw[key]
    lower = {str(key).lower(): value for key, value in raw.items()}
    for key in keys:
        value = lower.get(key.lower())
        if value is not None:
            return value
    return None


def _name_from_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        return _localized_name(value)
    if isinstance(value, dict):
        for key in ("name", "displayName", "shortName", "teamName", "playerName"):
            if key in value and value[key]:
                return str(value[key]).strip()
        if "Name" in value:
            return _localized_name(value["Name"])
    return str(value).strip() or None


def _localized_name(value: Any, locale: str = "en-GB") -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("Locale") == locale and item.get("Description"):
                return str(item["Description"]).strip()
        for item in value:
            if isinstance(item, dict) and item.get("Description"):
                return str(item["Description"]).strip()
    if isinstance(value, dict):
        if value.get("Description"):
            return str(value["Description"]).strip()
        if value.get("Name"):
            return _localized_name(value["Name"], locale=locale)
    return None


def _normalize_side(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"home", "h", "home_team", "hometeam"}:
        return "home"
    if text in {"away", "a", "away_team", "awayteam"}:
        return "away"
    return None


def _side_for_team(team: str | None, home_team: str | None, away_team: str | None) -> str | None:
    if not team:
        return None
    t = _norm(team)
    if home_team and t == _norm(home_team):
        return "home"
    if away_team and t == _norm(away_team):
        return "away"
    return None


def _team_for_side(side: str | None, home_team: str | None, away_team: str | None) -> str | None:
    if side == "home":
        return home_team
    if side == "away":
        return away_team
    return None


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace("%", "").strip()))
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "starter", "starting"}:
        return True
    if text in {"0", "false", "no", "n", "substitute"}:
        return False
    return None


def _extract_report_links(payload: Any) -> list[str]:
    links: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key.lower() in {"url", "href", "link", "report_url", "reportUrl"}:
                    if isinstance(child, str) and child.startswith(("http://", "https://")):
                        links.append(child)
                visit(child)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return sorted(set(links))
