"""Unit tests for deterministic probability-fusion helpers."""

from __future__ import annotations

import pytest

from app.core.engine import attenuate_market_boost, run_core_fusion


def test_market_boost_attenuates_only_on_both_direction_conflicts():
    boost, applied = attenuate_market_boost(
        0.10,
        dc_enhancer_divergence_pp=24.0,
        dc_enhancer_direction_conflict=True,
        pre_market_probs={"home_win_prob": 0.50, "draw_prob": 0.25, "away_win_prob": 0.25},
        market_probs={"home": 0.30, "draw": 0.25, "away": 0.45},
    )

    assert applied is True
    assert boost == pytest.approx(0.06)


@pytest.mark.parametrize(
    ("divergence_pp", "component_conflict", "market_away"),
    [
        (10.0, True, 0.45),
        (24.0, False, 0.45),
        (24.0, True, 0.20),
    ],
)
def test_market_boost_is_unchanged_without_full_conflict(
    divergence_pp: float,
    component_conflict: bool,
    market_away: float,
):
    boost, applied = attenuate_market_boost(
        0.10,
        dc_enhancer_divergence_pp=divergence_pp,
        dc_enhancer_direction_conflict=component_conflict,
        pre_market_probs={"home": 0.50, "draw": 0.25, "away": 0.25},
        market_probs={"home": 0.55, "draw": 0.25, "away": market_away},
    )

    assert applied is False
    assert boost == pytest.approx(0.10)


def test_market_boost_rejects_invalid_attenuation():
    with pytest.raises(ValueError, match="attenuation"):
        attenuate_market_boost(
            0.10,
            dc_enhancer_divergence_pp=24.0,
            dc_enhancer_direction_conflict=True,
            pre_market_probs={"home": 0.50, "draw": 0.25, "away": 0.25},
            market_probs={"home": 0.25, "draw": 0.25, "away": 0.50},
            attenuation=1.2,
        )


def test_core_fusion_returns_negbin_component_probs_when_applied():
    result = run_core_fusion(
        dc_probs={"home_win_prob": 0.45, "draw_prob": 0.30, "away_win_prob": 0.25},
        dc_home_xg=1.4,
        dc_away_xg=1.1,
        dc_base_weight=0.7,
    )

    assert result.negbin_applied is True
    assert result.negbin_probs is not None
    assert set(result.negbin_probs) == {"home", "draw", "away"}
    assert sum(result.negbin_probs.values()) == pytest.approx(1.0)
