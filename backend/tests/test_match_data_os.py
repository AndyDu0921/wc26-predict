from __future__ import annotations

from app.services.match_data.fifa_official_provider import (
    candidate_fifa_api_urls,
    parse_fifa_provider_match_id,
)
from app.services.match_data.game_state import build_game_state_profile
from app.services.match_data.normalizer import normalize_official_payload
from app.services.match_data.rich_context import load_rich_postmatch_context
from app.services.match_data.schema import RawOfficialMatchData
from app.services.match_data.storage import (
    payload_hash,
    save_game_state_segments,
    save_normalized_match_data,
    save_raw_match_data,
)


def _argentina_egypt_payload():
    return {
        "events": [
            {"id": "e1", "minute": 15, "type": "goal", "team": "Egypt", "player": "Egypt scorer 1"},
            {"id": "e2", "minute": 21, "type": "penalty_saved", "team": "Argentina", "player": "Argentina taker"},
            {"id": "e3", "minute": 67, "type": "goal", "team": "Egypt", "player": "Egypt scorer 2"},
            {"id": "e4", "minute": 79, "type": "goal", "team": "Argentina", "player": "Argentina scorer 1"},
            {"id": "e5", "minute": 83, "type": "goal", "team": "Argentina", "player": "Argentina scorer 2"},
            {"id": "e6", "minute": "90+2", "type": "goal", "team": "Argentina", "player": "Argentina scorer 3"},
        ],
        "lineups": {
            "home": [
                {"name": "Argentina GK", "position": "GK", "starter": True, "minutes": 90},
            ],
            "away": [
                {"name": "Egypt GK", "position": "GK", "starter": True, "minutes": 90},
            ],
        },
        "player_statistics": [
            {"name": "Argentina scorer 3", "team": "Argentina", "goals": 1, "shots": 2, "xg": 0.42},
        ],
    }


def _fifa_name(value: str):
    return [{"Locale": "en-GB", "Description": value}]


def _fifa_players(prefix: str, count: int):
    players = []
    for idx in range(1, count + 1):
        players.append(
            {
                "IdPlayer": f"{prefix}{idx}",
                "PlayerName": _fifa_name(f"{prefix.upper()} Player {idx}"),
                "ShortName": _fifa_name(f"{prefix.upper()}{idx}"),
                "Status": 1 if idx <= 11 else 2,
                "Position": 0 if idx == 1 else (3 if idx >= 9 else 2),
                "ShirtNumber": idx,
                "Captain": idx == 10,
            }
        )
    return players


def _fifa_live_payload():
    return {
        "HomeTeam": {
            "TeamName": _fifa_name("Argentina"),
            "Score": 3,
            "Players": _fifa_players("h", 26),
            "Goals": [
                {"IdGoal": "g3", "Minute": "79'", "IdPlayer": "h4", "Period": 5, "Type": 0},
                {"IdGoal": "g4", "Minute": "83'", "IdPlayer": "h10", "Period": 5, "Type": 0},
                {"IdGoal": "g5", "Minute": "90'+2'", "IdPlayer": "h8", "Period": 5, "Type": 0},
            ],
            "Substitutions": [
                {"IdEvent": "hs1", "Minute": "46'", "IdPlayerOff": "h11", "IdPlayerOn": "h12", "Period": 5},
                {"IdEvent": "hs2", "Minute": "61'", "IdPlayerOff": "h9", "IdPlayerOn": "h13", "Period": 5},
                {"IdEvent": "hs3", "Minute": "70'", "IdPlayerOff": "h7", "IdPlayerOn": "h14", "Period": 5},
                {"IdEvent": "hs4", "Minute": "78'", "IdPlayerOff": "h6", "IdPlayerOn": "h15", "Period": 5},
                {"IdEvent": "hs5", "Minute": "88'", "IdPlayerOff": "h5", "IdPlayerOn": "h16", "Period": 5},
            ],
        },
        "AwayTeam": {
            "TeamName": _fifa_name("Egypt"),
            "Score": 2,
            "Players": _fifa_players("a", 24),
            "Goals": [
                {"IdGoal": "g1", "Minute": "15'", "IdPlayer": "a4", "Period": 3, "Type": 0},
                {"IdGoal": "g2", "Minute": "67'", "IdPlayer": "a9", "Period": 5, "Type": 0},
            ],
            "Bookings": [
                {"IdEvent": f"ab{idx}", "Minute": f"{20 + idx}'", "IdPlayer": f"a{idx}", "Period": 5, "Card": 1}
                for idx in range(1, 8)
            ],
            "Substitutions": [
                {"IdEvent": "as1", "Minute": "58'", "IdPlayerOff": "a11", "IdPlayerOn": "a12", "Period": 5},
                {"IdEvent": "as2", "Minute": "68'", "IdPlayerOff": "a10", "IdPlayerOn": "a13", "Period": 5},
                {"IdEvent": "as3", "Minute": "76'", "IdPlayerOff": "a8", "IdPlayerOn": "a14", "Period": 5},
                {"IdEvent": "as4", "Minute": "89'", "IdPlayerOff": "a7", "IdPlayerOn": "a15", "Period": 5},
            ],
        },
    }


def test_fifa_match_centre_url_parsing():
    url = "https://www.fifa.com/en/match-centre/match/17/285023/289288/400021528"

    assert parse_fifa_provider_match_id(url) == "400021528"
    candidates = candidate_fifa_api_urls(url, "400021528")

    assert candidates
    assert any("400021528" in item for item in candidates)


def test_game_state_detects_late_two_goal_comeback():
    normalized = normalize_official_payload(
        _argentina_egypt_payload(),
        match_id="194",
        provider="fifa_official",
        home_team="Argentina",
        away_team="Egypt",
    )

    profile = build_game_state_profile(
        match_id="194",
        events=normalized.events,
        shots=normalized.shots,
        final_home_goals=3,
        final_away_goals=2,
    )

    comeback = profile["comeback_profile"]
    assert comeback["comeback"] is True
    assert comeback["late_comeback"] is True
    assert comeback["max_deficit"] == 2
    assert comeback["last_trailing_minute"] == 79
    assert comeback["equalizer_minute"] == 83
    assert comeback["winning_goal_minute"] == 90
    assert profile["game_state_profile"]["score_at_75"] == {"home": 0, "away": 2}


def test_match_data_storage_is_idempotent_and_context_loads(tmp_path):
    db_path = tmp_path / "match_data.db"
    payload = _argentina_egypt_payload()
    raw = RawOfficialMatchData(
        match_id="194",
        provider="fifa_official",
        provider_match_id="400021528",
        source_url="https://www.fifa.com/en/match-centre/match/17/285023/289288/400021528",
        payload=payload,
        payload_hash=payload_hash(payload),
        content_type="application/json; fixture",
        status="fixture",
    )

    first = save_raw_match_data(db_path, raw)
    second = save_raw_match_data(db_path, raw)
    normalized = normalize_official_payload(
        payload,
        match_id="194",
        provider="fifa_official",
        home_team="Argentina",
        away_team="Egypt",
    )
    counts = save_normalized_match_data(db_path, normalized)
    profile = build_game_state_profile(
        match_id="194",
        events=normalized.events,
        shots=normalized.shots,
        final_home_goals=3,
        final_away_goals=2,
    )
    stored_segments = save_game_state_segments(db_path, "194", profile["segments"])

    context = load_rich_postmatch_context(
        db_path,
        match_id="194",
        home_team="Argentina",
        away_team="Egypt",
        home_score=3,
        away_score=2,
    )

    assert first["action"] == "inserted"
    assert second["action"] == "existing"
    assert counts["events"] == 6
    assert counts["lineups"] == 2
    assert stored_segments == 8
    assert context["available"] is True
    assert context["tier"] == "rich_partial"
    assert context["coverage"]["technical_player_statistics"] is True
    assert context["coverage"]["true_shot_map"] is False
    assert "shot_events_from_event_timeline_only" in context["warnings"]
    assert context["comeback_profile"]["profile_label"] == "late_comeback"
    assert context["game_state_profile"]["data_scope"] == "postmatch_only"


def test_fifa_live_payload_parser_is_goal_timeline_not_full_shot_map(tmp_path):
    db_path = tmp_path / "fifa_live.db"
    payload = _fifa_live_payload()
    normalized = normalize_official_payload(
        payload,
        match_id="194",
        provider="fifa_official",
        home_team="Argentina",
        away_team="Egypt",
    )

    assert len(normalized.events) == 21
    assert len(normalized.shots) == 5
    assert len(normalized.lineups) == 50
    assert len(normalized.player_stats) == 5
    assert "fifa_live_payload_no_shot_map_xg" in normalized.warnings
    assert "fifa_live_player_stats_event_derived_only" in normalized.warnings
    assert "shot_events_from_goal_events_only" in normalized.warnings
    assert all(shot.payload["_match_data_os"]["derived_from_event"] is True for shot in normalized.shots)
    stoppage_goal = next(event for event in normalized.events if event.provider_event_id == "g5")
    assert stoppage_goal.minute == 90
    assert stoppage_goal.stoppage_minute == 2

    save_raw_match_data(
        db_path,
        RawOfficialMatchData(
            match_id="194",
            provider="fifa_official",
            provider_match_id="400021528",
            source_url="https://www.fifa.com/en/match-centre/match/17/285023/289288/400021528",
            payload=payload,
            payload_hash=payload_hash(payload),
            content_type="application/json; fixture",
            status="fixture",
        ),
    )
    save_normalized_match_data(db_path, normalized)
    profile = build_game_state_profile(
        match_id="194",
        events=normalized.events,
        shots=normalized.shots,
        final_home_goals=3,
        final_away_goals=2,
    )
    save_game_state_segments(db_path, "194", profile["segments"])

    context = load_rich_postmatch_context(
        db_path,
        match_id="194",
        home_team="Argentina",
        away_team="Egypt",
        home_score=3,
        away_score=2,
    )

    assert context["tier"] == "goal_timeline_complete"
    assert context["event_quality_score"] == 0.9
    assert context["coverage"]["goal_timeline"] is True
    assert context["coverage"]["true_shot_map"] is False
    assert context["coverage"]["shot_xg"] is False
    assert context["coverage"]["technical_player_statistics"] is False
    assert "full_shot_map" in context["missing"]
    assert "shot_xg" in context["missing"]
    assert "technical_player_statistics" in context["missing"]
    assert "player_stats_event_derived_only" in context["warnings"]
    assert context["game_state_profile"]["score_at_75"] == {"home": 0, "away": 2}
    assert context["comeback_profile"]["profile_label"] == "late_comeback"


def test_empty_rich_context_is_basic_only(tmp_path):
    context = load_rich_postmatch_context(tmp_path / "empty.db", match_id="missing")

    assert context["available"] is False
    assert context["tier"] == "basic_only"
    assert "event_timeline" in context["missing"]
