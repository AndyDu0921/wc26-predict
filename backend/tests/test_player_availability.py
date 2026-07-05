from app.services.player_availability import build_player_availability_shadow


def test_player_shadow_empty_dataset_has_no_effect():
    snapshot = build_player_availability_shadow(
        "Argentina",
        "Egypt",
        injury_records=[],
        player_catalog=[],
    )

    assert snapshot.home_xg_modifier == 0.0
    assert snapshot.away_xg_modifier == 0.0
    assert snapshot.adjustments == []
    assert snapshot.source_status["reason"] == "empty_availability_dataset"
    assert snapshot.source_status["shadow_only"] is True


def test_key_forward_absence_reduces_own_team_shadow_xg():
    snapshot = build_player_availability_shadow(
        "Argentina",
        "Egypt",
        injury_records=[
            {
                "player_name": "Lionel Example",
                "team_name": "Argentina",
                "status": "out",
                "confidence": 0.9,
                "source": "manual",
            }
        ],
        player_catalog=[
            {
                "player_name": "Lionel Example",
                "team_name": "Argentina",
                "position": "Forward",
                "importance_level": "key",
            }
        ],
    )

    assert snapshot.home_xg_modifier < 0
    assert snapshot.away_xg_modifier == 0.0
    assert snapshot.adjustments[0].availability_status == "out"
    assert snapshot.adjustments[0].expected_minutes_delta == -90
    assert snapshot.adjustments[0].importance_level == "key"
    assert snapshot.adjustments[0].position_group == "forward"
    assert snapshot.adjustments[0].xg_modifier < 0


def test_goalkeeper_absence_increases_opponent_shadow_xg():
    snapshot = build_player_availability_shadow(
        "Argentina",
        "Egypt",
        injury_records=[
            {
                "player_name": "Keeper Example",
                "team_name": "Egypt",
                "status": "doubtful",
                "confidence": 0.8,
                "source": "manual",
            }
        ],
        player_catalog=[
            {
                "player_name": "Keeper Example",
                "team_name": "Egypt",
                "position": "Goalkeeper",
                "importance_level": "starter",
            }
        ],
    )

    assert snapshot.home_xg_modifier > 0
    assert snapshot.away_xg_modifier == 0.0
    assert snapshot.adjustments[0].opponent_xg_modifier > 0
    assert snapshot.source_status["status"] == "used"


def test_player_shadow_filters_records_after_as_of_time():
    snapshot = build_player_availability_shadow(
        "Brazil",
        "Japan",
        as_of_time="2026-06-27T12:00:00+00:00",
        injury_records=[
            {
                "player_name": "Future Update",
                "team_name": "Brazil",
                "status": "out",
                "confidence": 1.0,
                "source": "manual",
                "last_updated": "2026-06-28T00:00:00+00:00",
            }
        ],
        player_catalog=[
            {
                "player_name": "Future Update",
                "team_name": "Brazil",
                "position": "Forward",
                "importance_level": "key",
            }
        ],
    )

    assert snapshot.home_xg_modifier == 0.0
    assert snapshot.away_xg_modifier == 0.0
    assert snapshot.adjustments == []
    assert snapshot.source_status["excluded_future_records"] == 1
