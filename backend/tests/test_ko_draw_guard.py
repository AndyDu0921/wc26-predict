"""Tests for post-calibration KO draw guard."""

from app.core.ko_draw_guard import (
    _is_ko_stage,
    check_ko_draw_guard,
)


def test_is_ko_stage_exact_names() -> None:
    """Known knockout stage names should be detected."""
    ko_stages = [
        "Round of 32",
        "Round of 16",
        "Quarter-finals",
        "Semi-finals",
        "Final",
        "Third Place",
    ]
    for stage in ko_stages:
        assert _is_ko_stage(stage), f"Expected '{stage}' to be KO"


def test_is_ko_stage_group_not_detected() -> None:
    """Group stage names should NOT be detected as KO."""
    group_stages = [
        "Group A - Matchday 1",
        "Group B - Matchday 3",
        "Group Stage",
    ]
    for stage in group_stages:
        assert not _is_ko_stage(stage), f"Expected '{stage}' NOT to be KO"


def test_is_ko_stage_none_and_empty() -> None:
    """None and empty string should not be KO."""
    assert not _is_ko_stage(None)
    assert not _is_ko_stage("")


def test_guard_not_triggered_for_non_ko() -> None:
    """Guard should not trigger for non-knockout matches."""
    result = check_ko_draw_guard(
        draw_prob=0.10,
        is_knockout=False,
        stage="Group A - Matchday 1",
        elo_gap=30,
    )
    assert result["checked"] is True
    assert result["triggered"] is False
    assert result["action"] == "none"


def test_guard_not_triggered_draw_above_floor() -> None:
    """If draw is above warning floor, should not trigger."""
    result = check_ko_draw_guard(
        draw_prob=0.25,
        is_knockout=True,
        elo_gap=30,
    )
    assert result["triggered"] is False


def test_guard_triggered_close_elo() -> None:
    """Close Elo gap + low draw in KO should trigger."""
    result = check_ko_draw_guard(
        draw_prob=0.15,
        is_knockout=True,
        elo_gap=30,
    )
    assert result["triggered"] is True
    assert "close Elo gap" in result["reason"]
    assert result["action"] == "warn_only"


def test_guard_triggered_low_xg() -> None:
    """Low total xG + low draw in KO should trigger."""
    result = check_ko_draw_guard(
        draw_prob=0.18,
        is_knockout=True,
        total_xg=2.0,
    )
    assert result["triggered"] is True
    assert "low total xG" in result["reason"]


def test_guard_triggered_market_disagreement() -> None:
    """Market draw high + model draw low in KO should trigger."""
    result = check_ko_draw_guard(
        draw_prob=0.19,        # 0.19 < 0.20 KO_DRAW_FLOOR_WARNING (V4.8.1)
        is_knockout=True,
        market_draw_prob=0.28,
    )
    assert result["triggered"] is True
    assert "market draw higher" in result["reason"]


def test_guard_triggered_model_disagreement() -> None:
    """High model disagreement in KO should trigger."""
    result = check_ko_draw_guard(
        draw_prob=0.19,
        is_knockout=True,
        model_disagreement=True,
    )
    assert result["triggered"] is True


def test_guard_no_trigger_without_risk_factors() -> None:
    """Low draw in KO without any risk factors should NOT trigger."""
    result = check_ko_draw_guard(
        draw_prob=0.18,
        is_knockout=True,
        elo_gap=150,     # wide gap
        total_xg=3.0,    # high scoring
    )
    assert result["triggered"] is False


def test_guard_resolves_stage_name() -> None:
    """When is_knockout=False but stage name is KO, should detect."""
    result = check_ko_draw_guard(
        draw_prob=0.15,
        is_knockout=False,
        stage="Quarter-finals",
        elo_gap=25,
    )
    assert result["triggered"] is True


def test_guard_multiple_risk_factors() -> None:
    """Multiple risk factors should all be listed."""
    result = check_ko_draw_guard(
        draw_prob=0.14,
        is_knockout=True,
        elo_gap=30,
        total_xg=1.8,
        market_draw_prob=0.27,
        model_disagreement=True,
    )
    assert result["triggered"] is True
    assert len(result["risk_factors"]) >= 3
