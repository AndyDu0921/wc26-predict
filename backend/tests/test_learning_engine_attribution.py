"""Regression tests for reproducible learning attribution."""

from __future__ import annotations

import asyncio
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


def test_historical_dixon_coles_alias_participates_in_attribution():
    class _Result:
        def mappings(self):
            return self

        def first(self):
            return None

    class _Db:
        def __init__(self):
            self.added = None

        async def execute(self, *args, **kwargs):
            return _Result()

        def add(self, value):
            self.added = value

    snapshot = _snapshot(
        id=None,
        match_id="197",
        baseline_probs={"home": 0.50, "draw": 0.25, "away": 0.25},
        adjusted_probs={"home": 0.50, "draw": 0.25, "away": 0.25},
        component_probs={
            "dixon_coles": {"home": 0.65, "draw": 0.20, "away": 0.15},
            "enhancer": {"home": 0.40, "draw": 0.30, "away": 0.30},
        },
        market_probs=None,
        fused_score_matrix=None,
        source_score_matrices=None,
        pipeline_params={
            "weight_config": {"dc": 0.60, "elo": 0.0, "pi": 0.0, "weibull": 0.0},
            "market_weight_used": 0.0,
        },
    )
    db = _Db()

    log = asyncio.run(
        LearningEngine()._attribute_error(
            snapshot,
            actual_index=0,
            db=db,
            verified_result_id=None,
            learning_weight=1.0,
            tier="full",
            home_goals=1,
            away_goals=0,
        )
    )

    assert db.added is log
    assert log.dc_marginal is not None
    assert log.model_was_right is True


def test_learning_log_records_wrong_prediction_boolean():
    class _Result:
        def mappings(self):
            return self

        def first(self):
            return None

    class _Db:
        def __init__(self):
            self.added = None

        async def execute(self, *args, **kwargs):
            return _Result()

        def add(self, value):
            self.added = value

    snapshot = _snapshot(
        id=None,
        match_id="200",
        baseline_probs={"home": 0.55, "draw": 0.20, "away": 0.25},
        adjusted_probs={"home": 0.55, "draw": 0.20, "away": 0.25},
        component_probs={},
        market_probs=None,
        fused_score_matrix=None,
        source_score_matrices=None,
        pipeline_params={},
    )
    db = _Db()

    log = asyncio.run(
        LearningEngine()._attribute_error(
            snapshot,
            actual_index=2,
            db=db,
            verified_result_id=None,
            learning_weight=1.0,
            tier="full",
            home_goals=1,
            away_goals=4,
        )
    )

    assert db.added is log
    assert log.model_was_right is False


def test_market_divergence_uses_multiclass_brier_not_home_only():
    class _Db:
        def __init__(self):
            self.added = None

        def add(self, value):
            self.added = value

    snapshot = _snapshot(
        match_id="market-test",
        baseline_probs={"home": 0.40, "draw": 0.50, "away": 0.10},
        market_probs={"home": 0.40, "draw": 0.10, "away": 0.50},
        pipeline_params={},
    )
    db = _Db()

    asyncio.run(
        LearningEngine()._log_market_divergence(
            snapshot,
            actual_index=1,
            db=db,
        )
    )

    assert db.added is not None
    assert db.added.model_home_prob == pytest.approx(0.40)
    assert db.added.market_home_prob == pytest.approx(0.40)
    assert db.added.model_was_closer is True
