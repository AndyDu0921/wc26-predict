"""Load rich post-match context for reports and learning logs."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.services.match_data.game_state import build_game_state_profile
from app.services.match_data.schema import MatchEvent, ShotEvent
from app.services.match_data.storage import count_rich_match_data, ensure_match_data_os_tables


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
    missing = _missing_from_counts(counts)
    if not events:
        return {
            "available": False,
            "tier": "basic_only",
            "counts": counts,
            "missing": missing,
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
    tier = _tier_from_counts(counts, profile["event_quality_score"])
    return {
        "available": True,
        "tier": tier,
        "counts": counts,
        "missing": missing,
        "event_quality_score": profile["event_quality_score"],
        "game_state_profile": {
            **profile["game_state_profile"],
            "home_team": home_team,
            "away_team": away_team,
            "rich_data_tier": tier,
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


def _missing_from_counts(counts: dict[str, int]) -> list[str]:
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
    if counts.get("player_stats", 0) == 0:
        missing.append("player_statistics")
    return missing


def _tier_from_counts(counts: dict[str, int], quality: float) -> str:
    if counts.get("events", 0) == 0:
        return "basic_only"
    if quality >= 0.80 and counts.get("lineups", 0) > 0 and counts.get("player_stats", 0) > 0:
        return "rich_complete"
    if quality >= 0.50:
        return "rich_partial"
    return "event_timeline_only"


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

