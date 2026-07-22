"""Unit tests for stacking feature engineering (pure functions)."""

from __future__ import annotations


import pytest

from app.core.stacking_features import (
    STACKING_FEATURE_KEYS,
    STACKING_META_LEARNER_ENABLED,
    STACKING_FEATURE_FILL,
    assemble_feature_vector,
    encode_actual_result,
    build_training_data_from_snapshots,
)


# ── Feature flag default ──────────────────────────────────────────────────

def test_feature_flag_defaults_to_false():
    """Stacking must be opt-in until proven on backtest."""
    assert STACKING_META_LEARNER_ENABLED is False, (
        "STACKING_META_LEARNER_ENABLED must default to False — "
        "the meta-learner must pass backtest validation before activation"
    )


# ── assemble_feature_vector ───────────────────────────────────────────────

def test_all_seven_components_present():
    """All 7 components → 21 floats in canonical order."""
    cp = {
        "dixon_coles": {"home": 0.50, "draw": 0.30, "away": 0.20},
        "enhancer":    {"home": 0.45, "draw": 0.25, "away": 0.30},
        "negbin":      {"home": 0.48, "draw": 0.32, "away": 0.20},
        "weibull":     {"home": 0.40, "draw": 0.35, "away": 0.25},
        "elo":         {"home": 0.55, "draw": 0.22, "away": 0.23},
        "pi_rating":   {"home": 0.42, "draw": 0.28, "away": 0.30},
    }
    mp = {"home_prob": 0.47, "draw_prob": 0.29, "away_prob": 0.24}

    feat = assemble_feature_vector(cp, mp)

    assert len(feat) == 21, f"Expected 21 features, got {len(feat)}"
    # Spot-check known positions
    assert feat[0] == pytest.approx(0.50)  # dc home
    assert feat[1] == pytest.approx(0.30)  # dc draw
    assert feat[2] == pytest.approx(0.20)  # dc away
    assert feat[3] == pytest.approx(0.45)  # enh home
    # Market is last (indices 18, 19, 20)
    assert feat[18] == pytest.approx(0.47)  # market home
    assert feat[19] == pytest.approx(0.29)  # market draw
    assert feat[20] == pytest.approx(0.24)  # market away


def test_missing_component_filled_with_uniform():
    """A missing component gets STACKING_FEATURE_FILL for each outcome."""
    cp = {"dixon_coles": {"home": 0.60, "draw": 0.25, "away": 0.15}}
    feat = assemble_feature_vector(cp)
    # enhancer (indices 3,4,5) should be fill
    assert feat[3] == pytest.approx(STACKING_FEATURE_FILL)
    assert feat[4] == pytest.approx(STACKING_FEATURE_FILL)
    assert feat[5] == pytest.approx(STACKING_FEATURE_FILL)


def test_market_probs_embedded_in_component_probs():
    """When market_probs is not passed separately, look inside component_probs."""
    cp = {
        "dixon_coles": {"home": 0.50, "draw": 0.30, "away": 0.20},
        "market": {"home_prob": 0.44, "draw_prob": 0.33, "away_prob": 0.23},
    }
    feat = assemble_feature_vector(cp)  # no market_probs arg
    assert feat[18] == pytest.approx(0.44)
    assert feat[19] == pytest.approx(0.33)
    assert feat[20] == pytest.approx(0.23)


def test_market_probs_argument_overrides_embedded():
    """Explicit market_probs argument takes priority over component_probs['market']."""
    cp = {"market": {"home_prob": 0.10, "draw_prob": 0.10, "away_prob": 0.80}}
    mp = {"home_prob": 0.50, "draw_prob": 0.25, "away_prob": 0.25}
    feat = assemble_feature_vector(cp, mp)
    assert feat[18] == pytest.approx(0.50)
    assert feat[19] == pytest.approx(0.25)
    assert feat[20] == pytest.approx(0.25)


def test_short_form_keys_accepted():
    """Both 'home'/'draw'/'away' and 'home_win_prob'/'draw_prob'/'away_win_prob' work."""
    cp = {
        "dixon_coles": {"home_win_prob": 0.70, "draw_prob": 0.20, "away_win_prob": 0.10},
    }
    feat = assemble_feature_vector(cp)
    assert feat[0] == pytest.approx(0.70)
    assert feat[1] == pytest.approx(0.20)
    assert feat[2] == pytest.approx(0.10)


def test_empty_component_probs_returns_all_fill():
    """Empty input → all 21 slots are fill."""
    feat = assemble_feature_vector({})
    assert len(feat) == 21
    for val in feat:
        assert val == pytest.approx(STACKING_FEATURE_FILL)


def test_feature_order_matches_canonical_keys():
    """The output order must match STACKING_FEATURE_KEYS exactly."""
    cp = {}
    feat = assemble_feature_vector(cp)
    # 7 components × 3 outcomes = 21
    assert len(feat) == len(STACKING_FEATURE_KEYS) * 3


# ── encode_actual_result ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("input_val", "expected"),
    [
        ("H", 0), ("HOME", 0), ("home_win", 0), ("1", 0),
        ("D", 1), ("DRAW", 1), ("X", 1), ("2", 1),
        ("A", 2), ("AWAY", 2), ("away_win", 2), ("3", 2),
        (" h ", 0), (" Draw ", 1),
    ],
)
def test_encode_actual_result(input_val: str, expected: int):
    assert encode_actual_result(input_val) == expected


def test_encode_actual_result_rejects_invalid():
    with pytest.raises(ValueError, match="Unknown actual_result"):
        encode_actual_result("INVALID")


# ── build_training_data_from_snapshots ────────────────────────────────────

def test_build_training_data_basic():
    snapshots = [
        {
            "component_probs": {
                "dixon_coles": {"home": 0.50, "draw": 0.30, "away": 0.20},
            },
            "actual_result": "H",
        },
        {
            "component_probs": {
                "dixon_coles": {"home": 0.20, "draw": 0.30, "away": 0.50},
            },
            "actual_result": "A",
        },
    ]
    X, y = build_training_data_from_snapshots(snapshots)
    assert len(X) == 2
    assert len(y) == 2
    assert y == [0, 2]


def test_build_training_data_skips_invalid():
    """Snapshots without component_probs or actual_result are skipped."""
    snapshots = [
        {"component_probs": None, "actual_result": "H"},          # no component_probs
        {"component_probs": {"dc": {"home": 0.5, "draw": 0.3, "away": 0.2}}},  # no actual_result
        {
            "component_probs": {"dixon_coles": {"home": 0.5, "draw": 0.3, "away": 0.2}},
            "actual_result": "D",
        },
    ]
    X, y = build_training_data_from_snapshots(snapshots)
    assert len(X) == 1
    assert y == [1]


def test_build_training_data_includes_market():
    snapshots = [
        {
            "component_probs": {
                "dixon_coles": {"home": 0.50, "draw": 0.30, "away": 0.20},
                "market": {"home_prob": 0.45, "draw_prob": 0.30, "away_prob": 0.25},
            },
            "market_probs": {"home_prob": 0.44, "draw_prob": 0.31, "away_prob": 0.25},
            "actual_result": "H",
        },
    ]
    X, y = build_training_data_from_snapshots(snapshots)
    assert len(X) == 1
    # Market features (indices 18-20) use explicit market_probs, not embedded
    assert X[0][18] == pytest.approx(0.44)
