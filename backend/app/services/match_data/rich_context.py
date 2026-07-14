"""Load rich post-match context for reports and learning logs."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.services.match_data.game_state import build_game_state_profile
from app.services.match_data.schema import MatchEvent, ShotEvent
from app.services.match_data.storage import count_rich_match_data, ensure_match_data_os_tables

GOAL_EVENT_TYPES = {"goal", "penalty_goal", "own_goal"}
PASSING_TIERS = {"goal_timeline_complete", "rich_partial", "rich_complete"}


def load_rich_postmatch_context(
    db_path: str | Path,
    *,
    match_id: str,
    home_team: str | None = None,
    away_team: str | None = None,
    home_score: int | None = None,
    away_score: int | None = None,
) -> dict[str, Any]:
    """Return rich post-match diagnostics for one match.

    This function is read-only.  It derives profiles in memory and is safe to
    call from the canonical post-match pipeline.
    """
    ensure_match_data_os_tables(db_path)
    counts = count_rich_match_data(db_path, str(match_id))
    events = _load_events(db_path, str(match_id))
    shots = _load_shots(db_path, str(match_id))
    coverage = _coverage_from_data(db_path, str(match_id), counts, events, shots)
    missing = _missing_from_counts(counts, coverage)
    if not events:
        return {
            "available": False,
            "tier": "basic_only",
            "counts": counts,
            "missing": missing,
            "coverage": coverage,
            "warnings": coverage["warnings"],
            "event_quality_score": 0.0,
            "game_state_profile": {"data_scope": "postmatch_only", "events": 0, "shots": len(shots)},
            "comeback_profile": {"comeback": False, "profile_label": "unavailable"},
            "goal_timeline": [],
            "segment_summary": [],
        }
    profile = build_game_state_profile(
        match_id=str(match_id),
        events=events,
        shots=shots,
        final_home_goals=home_score,
        final_away_goals=away_score,
    )
    tier = _tier_from_counts(counts, profile["event_quality_score"], coverage)
    return {
        "available": True,
        "tier": tier,
        "counts": counts,
        "missing": missing,
        "coverage": coverage,
        "warnings": coverage["warnings"],
        "event_quality_score": profile["event_quality_score"],
        "game_state_profile": {
            **profile["game_state_profile"],
            "home_team": home_team,
            "away_team": away_team,
            "rich_data_tier": tier,
            "coverage": coverage,
        },
        "comeback_profile": profile["comeback_profile"],
        "goal_timeline": profile["goal_timeline"],
        "segment_summary": [_segment_to_summary(segment) for segment in profile["segments"]],
    }


def _load_events(db_path: str | Path, match_id: str) -> list[MatchEvent]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM match_events WHERE match_id=? ORDER BY COALESCE(minute, 0), COALESCE(stoppage_minute, 0)",
            (str(match_id),),
        ).fetchall()
    return [
        MatchEvent(
            match_id=str(row["match_id"]),
            provider=row["provider"],
            provider_event_id=row["provider_event_id"],
            minute=row["minute"],
            stoppage_minute=row["stoppage_minute"],
            period=row["period"],
            team_name=row["team_name"],
            side=row["side"],
            player_name=row["player_name"],
            related_player_name=row["related_player_name"],
            event_type=row["event_type"],
            outcome=row["outcome"],
            xg=row["xg"],
            home_score_after=row["home_score_after"],
            away_score_after=row["away_score_after"],
            payload=_json_load(row["payload_json"]),
        )
        for row in rows
    ]


def _load_shots(db_path: str | Path, match_id: str) -> list[ShotEvent]:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM shot_events WHERE match_id=? ORDER BY COALESCE(minute, 0), COALESCE(stoppage_minute, 0)",
            (str(match_id),),
        ).fetchall()
    return [
        ShotEvent(
            match_id=str(row["match_id"]),
            provider=row["provider"],
            event_id=row["event_id"],
            minute=row["minute"],
            stoppage_minute=row["stoppage_minute"],
            period=row["period"],
            team_name=row["team_name"],
            side=row["side"],
            player_name=row["player_name"],
            xg=row["xg"],
            outcome=row["outcome"],
            body_part=row["body_part"],
            shot_type=row["shot_type"],
            assist_player=row["assist_player"],
            home_score_after=row["home_score_after"],
            away_score_after=row["away_score_after"],
            payload=_json_load(row["payload_json"]),
        )
        for row in rows
    ]


def _missing_from_counts(counts: dict[str, int], coverage: dict[str, Any]) -> list[str]:
    missing = []
    if counts.get("raw", 0) == 0:
        missing.append("official_raw_payload")
    if counts.get("events", 0) == 0:
        missing.append("event_timeline")
    if counts.get("lineups", 0) == 0:
        missing.append("lineups")
    if counts.get("player_minutes", 0) == 0:
        missing.append("player_minutes")
    if counts.get("shots", 0) == 0:
        missing.append("shot_events")
    if not coverage.get("true_shot_map"):
        missing.append("full_shot_map")
    if not coverage.get("shot_xg"):
        missing.append("shot_xg")
    if not coverage.get("technical_player_statistics"):
        missing.append("technical_player_statistics")
    return missing


def _tier_from_counts(counts: dict[str, int], quality: float, coverage: dict[str, Any]) -> str:
    if counts.get("events", 0) == 0:
        return "basic_only"
    if (
        quality >= 0.80
        and counts.get("lineups", 0) > 0
        and counts.get("player_minutes", 0) > 0
        and coverage.get("true_shot_map")
        and coverage.get("shot_xg")
        and coverage.get("technical_player_statistics")
    ):
        return "rich_complete"
    if (
        quality >= 0.50
        and counts.get("lineups", 0) > 0
        and (coverage.get("true_shot_map") or coverage.get("technical_player_statistics"))
    ):
        return "rich_partial"
    if coverage.get("goal_timeline") and counts.get("lineups", 0) > 0 and counts.get("player_minutes", 0) > 0:
        return "goal_timeline_complete"
    return "event_timeline_only"


def _coverage_from_data(
    db_path: str | Path,
    match_id: str,
    counts: dict[str, int],
    events: list[MatchEvent],
    shots: list[ShotEvent],
) -> dict[str, Any]:
    goals = [event for event in events if event.event_type in GOAL_EVENT_TYPES]
    derived_shots_only = _shots_are_event_derived(shots, goals)
    shot_xg = any(shot.xg is not None for shot in shots)
    technical_player_stats = _has_technical_player_statistics(db_path, match_id)
    true_shot_map = bool(shots and not derived_shots_only)
    warnings = []
    if shots and derived_shots_only:
        warnings.append("shot_events_from_event_timeline_only")
    if shots and not shot_xg:
        warnings.append("no_shot_xg")
    if not true_shot_map:
        warnings.append("no_full_shot_map")
    if counts.get("player_stats", 0) > 0 and not technical_player_stats:
        warnings.append("player_stats_event_derived_only")
    if counts.get("player_stats", 0) == 0:
        warnings.append("no_player_statistics_found")
    elif not technical_player_stats:
        warnings.append("no_technical_player_statistics")
    return {
        "event_timeline": bool(events),
        "goal_timeline": bool(goals),
        "lineups": counts.get("lineups", 0) > 0,
        "player_minutes": counts.get("player_minutes", 0) > 0,
        "shot_events": bool(shots),
        "true_shot_map": true_shot_map,
        "shot_xg": shot_xg,
        "technical_player_statistics": technical_player_stats,
        "event_derived_shots_only": derived_shots_only,
        "warnings": sorted(set(warnings)),
    }


def _shots_are_event_derived(shots: list[ShotEvent], goals: list[MatchEvent]) -> bool:
    if not shots:
        return False
    if all((shot.payload or {}).get("_match_data_os", {}).get("derived_from_event") for shot in shots):
        return True
    if not goals:
        return False
    # Compatibility for rows normalized before the marker existed: FIFA live
    # goal-derived shots carry no xG/body-part/shot-type detail and have no
    # non-goal shot volume beyond the goal timeline.
    return (
        len(shots) <= len(goals)
        and all(shot.xg is None and shot.body_part is None and shot.shot_type is None for shot in shots)
        and all(str(shot.outcome or "").lower() in GOAL_EVENT_TYPES for shot in shots)
    )


def _has_technical_player_statistics(db_path: str | Path, match_id: str) -> bool:
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT stats_json FROM match_player_statistics WHERE match_id=?",
            (str(match_id),),
        ).fetchall()
    if not rows:
        return False
    for row in rows:
        stats = _json_load(row["stats_json"])
        if stats.get("source") == "fifa_live_events":
            continue
        available_fields = stats.get("available_fields")
        if isinstance(available_fields, list) and set(available_fields).issubset({"goals", "assists"}):
            continue
        return True
    return False


def _segment_to_summary(segment) -> dict[str, Any]:
    return {
        "window": f"{segment.minute_start}-{segment.minute_end}",
        "score_start": f"{segment.home_score_start}-{segment.away_score_start}",
        "score_end": f"{segment.home_score_end}-{segment.away_score_end}",
        "leader_start": segment.leader_start,
        "leader_end": segment.leader_end,
        "home_shots": segment.home_shots,
        "away_shots": segment.away_shots,
        "home_xg": segment.home_xg,
        "away_xg": segment.away_xg,
        "event_types": segment.state.get("event_types", {}),
    }


def _json_load(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}

