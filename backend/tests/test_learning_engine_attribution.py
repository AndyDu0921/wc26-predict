"""Regression tests for reproducible learning attribution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.learning_engine import LearningEngine


def _snapshot(**overrides):
    values = {
        "home_team": "France",
        "away_team": "Brazil",
        "competition": "FIFA World Cup 2026",
        "pipeline_params": {},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_historical_weights_take_precedence_over_current_config():
    snapshot = _snapshot(
        pipeline_params={
            "weight_config": {
                "dc": 0.70,
                "elo": 0.10,
                "pi": 0.15,
                "weibull": 0.08,
                "market_max": 0.30,
            },
            "market_weight_used": 0.22,
            "negbin_weight": 0.05,
        }
    )

    weights, source = LearningEngine._weights_for_snapshot(
        snapshot,
        {"dc": {}, "enhancer": {}, "negbin": {}, "market": {}},
    )

    assert source == "snapshot"
    assert weights["dc"] == pytest.approx(0.70)
    assert weights["enhancer"] == pytest.approx(0.30)
    assert weights["market"] == pytest.approx(0.22)
    assert weights["negbin"] == pytest.approx(0.05)


def test_pi_and_negbin_are_in_sequential_reconstruction():
    components = {
        "dc": {"home": 0.60, "draw": 0.25, "away": 0.15},
        "enhancer": {"home": 0.40, "draw": 0.30, "away": 0.30},
        "negbin": {"home": 0.55, "draw": 0.25, "away": 0.20},
        "pi": {"home": 0.20, "draw": 0.30, "away": 0.50},
    }
    weights = {
        "enhancer": 0.30,
        "negbin": 0.05,
        "pi": 0.20,
    }

    with_pi = LearningEngine._fuse_without(
        components,
        weights,
        exclude="market",
    )
    without_pi = LearningEngine._fuse_without(
        components,
        weights,
        exclude="pi",
    )

    assert with_pi is not None
    assert without_pi is not None
    assert with_pi["away"] > without_pi["away"]
    assert sum(with_pi.values()) == pytest.approx(1.0)
