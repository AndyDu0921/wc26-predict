from app.services.information_state_signals import build_information_state_signals


def test_information_state_signals_include_only_pre_asof_evidence():
    payload = build_information_state_signals(
        "Brazil",
        "Japan",
        as_of_time="2026-06-28T12:00:00+00:00",
        kickoff_at="2026-06-29T20:00:00+00:00",
        injury_records=[
            {
                "player_name": "Current Forward",
                "team_name": "Brazil",
                "status": "out",
                "injury_type": "hamstring",
                "confidence": 0.9,
                "source": "manual",
                "source_url": "https://example.test/current",
                "last_updated": "2026-06-28T08:00:00+00:00",
            },
            {
                "player_name": "Future Forward",
                "team_name": "Brazil",
                "status": "out",
                "confidence": 0.9,
                "source": "manual",
                "last_updated": "2026-06-28T18:00:00+00:00",
            },
        ],
    )

    assert payload["schema_version"] == "information_state_signals.v1"
    assert len(payload["signals"]) == 1
    signal = payload["signals"][0]
    assert signal["affected_player"] == "Current Forward"
    assert signal["source_url"] == "https://example.test/current"
    assert signal["included_in_strict_features"] is True
    assert signal["shadow_only"] is True
    assert payload["summary"]["excluded_future_signals"] == 1


def test_information_state_signals_after_kickoff_are_not_strict():
    payload = build_information_state_signals(
        "Alpha",
        "Beta",
        as_of_time="2026-07-02T10:00:00+00:00",
        kickoff_at="2026-07-01T20:00:00+00:00",
        injury_records=[
            {
                "player_name": "Late Update",
                "team_name": "Alpha",
                "status": "doubtful",
                "confidence": 0.8,
                "source": "manual",
                "last_updated": "2026-07-02T09:00:00+00:00",
            },
        ],
    )

    assert len(payload["signals"]) == 1
    assert payload["signals"][0]["included_in_strict_features"] is False
    assert payload["signals"][0]["source_status"] == "used_but_not_strict"
