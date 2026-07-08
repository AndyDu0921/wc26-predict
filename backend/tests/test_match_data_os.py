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
    assert context["tier"] == "rich_complete"
    assert context["comeback_profile"]["profile_label"] == "late_comeback"
    assert context["game_state_profile"]["data_scope"] == "postmatch_only"


def test_empty_rich_context_is_basic_only(tmp_path):
    context = load_rich_postmatch_context(tmp_path / "empty.db", match_id="missing")

    assert context["available"] is False
    assert context["tier"] == "basic_only"
    assert "event_timeline" in context["missing"]
