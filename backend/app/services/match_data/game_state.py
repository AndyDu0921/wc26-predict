"""Game-state derivation from normalized event streams."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.services.match_data.schema import GameStateSegment, MatchEvent, ShotEvent


SEGMENT_WINDOWS: tuple[tuple[int, int], ...] = (
    (0, 15),
    (16, 30),
    (31, 45),
    (46, 60),
    (61, 75),
    (76, 90),
    (91, 105),
    (106, 120),
)

GOAL_EVENT_TYPES = {"goal", "penalty_goal", "own_goal"}
CARD_EVENT_TYPES = {"yellow_card", "red_card"}


def build_game_state_profile(
    *,
    match_id: str,
    events: list[MatchEvent],
    shots: list[ShotEvent] | None = None,
    final_home_goals: int | None = None,
    final_away_goals: int | None = None,
) -> dict[str, Any]:
    """Build game-state segments and comeback diagnostics.

    The output is post-match-only learning metadata.  It must never be joined
    into pre-match strict snapshots for the same match.
    """
    shots = shots or []
    sorted_events = sorted(events, key=_event_sort_key)
    scored_events = _events_with_running_score(sorted_events)
    segments = _build_segments(match_id, scored_events, shots)
    timeline = _goal_timeline(scored_events)
    if final_home_goals is None or final_away_goals is None:
        final_home_goals, final_away_goals = _final_score_from_events(scored_events)
    comeback_profile = _comeback_profile(
        timeline,
        final_home_goals=final_home_goals,
        final_away_goals=final_away_goals,
    )
    event_quality_score = _event_quality_score(events, shots)
    game_state_profile = {
        "data_scope": "postmatch_only",
        "segments": len(segments),
        "events": len(events),
        "shots": len(shots),
        "goals_in_timeline": len(timeline),
        "final_score": {
            "home": final_home_goals,
            "away": final_away_goals,
        },
        "score_at_15": _score_at_minute(timeline, 15),
        "score_at_ht": _score_at_minute(timeline, 45),
        "score_at_60": _score_at_minute(timeline, 60),
        "score_at_75": _score_at_minute(timeline, 75),
        "score_at_90": _score_at_minute(timeline, 90),
    }
    return {
        "segments": segments,
        "game_state_profile": game_state_profile,
        "comeback_profile": comeback_profile,
        "goal_timeline": timeline,
        "event_quality_score": event_quality_score,
    }


def _events_with_running_score(events: list[MatchEvent]) -> list[MatchEvent]:
    home_score = 0
    away_score = 0
    output: list[MatchEvent] = []
    for event in events:
        if event.event_type in GOAL_EVENT_TYPES:
            scoring_side = _scoring_side(event)
            if scoring_side == "home":
                home_score += 1
            elif scoring_side == "away":
                away_score += 1
        output.append(
            replace(
                event,
                home_score_after=event.home_score_after if event.home_score_after is not None else home_score,
                away_score_after=event.away_score_after if event.away_score_after is not None else away_score,
            )
        )
    return output


def _build_segments(
    match_id: str,
    events: list[MatchEvent],
    shots: list[ShotEvent],
) -> list[GameStateSegment]:
    goal_timeline = _goal_timeline(events)
    segments: list[GameStateSegment] = []
    for start, end in SEGMENT_WINDOWS:
        segment_events = [event for event in events if _event_in_window(event, start, end)]
        segment_shots = [shot for shot in shots if _shot_in_window(shot, start, end)]
        score_start = _score_before_minute(goal_timeline, start)
        score_end = _score_at_minute(goal_timeline, end)
        home_xg = _sum_xg(shot for shot in segment_shots if shot.side == "home")
        away_xg = _sum_xg(shot for shot in segment_shots if shot.side == "away")
        segments.append(
            GameStateSegment(
                match_id=str(match_id),
                minute_start=start,
                minute_end=end,
                period=_period_for_window(start, end),
                home_score_start=score_start["home"],
                away_score_start=score_start["away"],
                home_score_end=score_end["home"],
                away_score_end=score_end["away"],
                leader_start=_leader(score_start["home"], score_start["away"]),
                leader_end=_leader(score_end["home"], score_end["away"]),
                home_events_count=sum(1 for event in segment_events if event.side == "home"),
                away_events_count=sum(1 for event in segment_events if event.side == "away"),
                home_shots=sum(1 for shot in segment_shots if shot.side == "home"),
                away_shots=sum(1 for shot in segment_shots if shot.side == "away"),
                home_xg=home_xg,
                away_xg=away_xg,
                cards_count=sum(1 for event in segment_events if event.event_type in CARD_EVENT_TYPES),
                substitutions_count=sum(1 for event in segment_events if event.event_type == "substitution"),
                state={
                    "postmatch_only": True,
                    "event_types": _event_type_counts(segment_events),
                },
            )
        )
    return segments


def _goal_timeline(events: list[MatchEvent]) -> list[dict[str, Any]]:
    timeline = []
    home = 0
    away = 0
    for event in events:
        if event.event_type not in GOAL_EVENT_TYPES:
            continue
        scoring_side = _scoring_side(event)
        if scoring_side == "home":
            home += 1
        elif scoring_side == "away":
            away += 1
        timeline.append(
            {
                "minute": event.minute,
                "stoppage_minute": event.stoppage_minute,
                "display_minute": _display_minute(event.minute, event.stoppage_minute),
                "side": scoring_side,
                "team": event.team_name,
                "player": event.player_name,
                "event_type": event.event_type,
                "home": home,
                "away": away,
                "leader": _leader(home, away),
            }
        )
    return timeline


def _comeback_profile(
    timeline: list[dict[str, Any]],
    *,
    final_home_goals: int,
    final_away_goals: int,
) -> dict[str, Any]:
    final_winner = _leader(final_home_goals, final_away_goals)
    if final_winner == "draw":
        return {
            "comeback": False,
            "final_winner": "draw",
            "max_deficit": 0,
            "late_comeback": False,
        }
    max_deficit = 0
    last_trailing_minute = None
    equalizer_minute = None
    winning_goal_minute = None
    was_trailing = False
    for point in timeline:
        home = int(point["home"])
        away = int(point["away"])
        deficit = away - home if final_winner == "home" else home - away
        minute = point["minute"]
        if deficit > 0:
            was_trailing = True
            max_deficit = max(max_deficit, deficit)
            last_trailing_minute = minute
        elif was_trailing and deficit == 0 and equalizer_minute is None:
            equalizer_minute = minute
        elif was_trailing and deficit < 0 and winning_goal_minute is None:
            winning_goal_minute = minute
    return {
        "comeback": max_deficit > 0,
        "final_winner": final_winner,
        "max_deficit": max_deficit,
        "last_trailing_minute": last_trailing_minute,
        "equalizer_minute": equalizer_minute,
        "winning_goal_minute": winning_goal_minute,
        "late_comeback": bool(max_deficit > 0 and last_trailing_minute is not None and last_trailing_minute >= 75),
        "profile_label": _profile_label(max_deficit, last_trailing_minute, winning_goal_minute),
    }


def _profile_label(max_deficit: int, last_trailing_minute: int | None, winning_goal_minute: int | None) -> str:
    if max_deficit <= 0:
        return "no_comeback"
    if last_trailing_minute is not None and last_trailing_minute >= 75:
        return "late_comeback"
    if winning_goal_minute is not None and winning_goal_minute >= 90:
        return "stoppage_time_decider"
    return "comeback"


def _event_quality_score(events: list[MatchEvent], shots: list[ShotEvent]) -> float:
    score = 0.0
    if events:
        score += 0.35
    if any(event.event_type in GOAL_EVENT_TYPES for event in events):
        score += 0.25
    if shots:
        score += 0.20
    if any(shot.xg is not None for shot in shots):
        score += 0.10
    if any(event.event_type in {"substitution", "red_card", "yellow_card"} for event in events):
        score += 0.10
    return round(min(score, 1.0), 4)


def _score_at_minute(timeline: list[dict[str, Any]], minute: int) -> dict[str, int]:
    score = {"home": 0, "away": 0}
    for point in timeline:
        point_minute = point.get("minute")
        if point_minute is not None and int(point_minute) <= minute:
            score = {"home": int(point["home"]), "away": int(point["away"])}
    return score


def _score_before_minute(timeline: list[dict[str, Any]], minute: int) -> dict[str, int]:
    score = {"home": 0, "away": 0}
    for point in timeline:
        point_minute = point.get("minute")
        if point_minute is not None and int(point_minute) < minute:
            score = {"home": int(point["home"]), "away": int(point["away"])}
    return score


def _final_score_from_events(events: list[MatchEvent]) -> tuple[int, int]:
    timeline = _goal_timeline(events)
    if not timeline:
        return 0, 0
    last = timeline[-1]
    return int(last["home"]), int(last["away"])


def _scoring_side(event: MatchEvent) -> str | None:
    if event.event_type == "own_goal":
        if event.side == "home":
            return "away"
        if event.side == "away":
            return "home"
    return event.side


def _event_sort_key(event: MatchEvent) -> tuple[int, int, str]:
    return (event.minute or 0, event.stoppage_minute or 0, event.event_type)


def _event_in_window(event: MatchEvent, start: int, end: int) -> bool:
    minute = event.minute
    if minute is None:
        return False
    if start <= minute <= end:
        return True
    return end == 90 and minute == 90


def _shot_in_window(shot: ShotEvent, start: int, end: int) -> bool:
    minute = shot.minute
    return minute is not None and start <= minute <= end


def _sum_xg(shots) -> float | None:
    values = [shot.xg for shot in shots if shot.xg is not None]
    if not values:
        return None
    return round(sum(float(value) for value in values), 4)


def _period_for_window(start: int, end: int) -> str:
    if end <= 45:
        return "first_half"
    if end <= 90:
        return "second_half"
    if end <= 105:
        return "extra_time_first_half"
    return "extra_time_second_half"


def _leader(home: int, away: int) -> str:
    if home > away:
        return "home"
    if away > home:
        return "away"
    return "draw"


def _event_type_counts(events: list[MatchEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.event_type] = counts.get(event.event_type, 0) + 1
    return counts


def _display_minute(minute: int | None, stoppage: int | None) -> str:
    if minute is None:
        return "N/A"
    if stoppage:
        return f"{minute}+{stoppage}"
    return str(minute)

