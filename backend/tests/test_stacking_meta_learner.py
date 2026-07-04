"""Tests for stacking meta-learner safety gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.stacking_features import STACKING_FEATURE_KEYS
from app.services.stacking_meta_learner import StackingMetaLearner


FEATURE_COUNT = len(STACKING_FEATURE_KEYS) * 3


def _features(n: int) -> list[list[float]]:
    return [[1.0 / 3.0] * FEATURE_COUNT for _ in range(n)]


def test_fit_refuses_training_data_without_all_three_outcomes() -> None:
    learner = StackingMetaLearner()

    learner.fit(_features(20), [0] * 10 + [2] * 10)

    assert learner.is_fitted is False
    probs = learner.predict_proba({})
    assert probs["home_win_prob"] == pytest.approx(1.0 / 3.0)
    assert probs["draw_prob"] == pytest.approx(1.0 / 3.0)
    assert probs["away_win_prob"] == pytest.approx(1.0 / 3.0)


def test_load_invalid_two_class_artifact_is_unfitted(tmp_path: Path) -> None:
    artifact = tmp_path / "stacking_meta_learner.json"
    artifact.write_text(
        json.dumps(
            {
                "coef": [[0.0] * FEATURE_COUNT, [0.0] * FEATURE_COUNT],
                "intercept": [0.0, 0.0],
                "classes": [0, 2],
                "is_fitted": True,
                "training_sample_count": 50,
            }
        ),
        encoding="utf-8",
    )

    learner = StackingMetaLearner()
    learner.load(str(artifact))

    assert learner.is_fitted is False
    assert set(learner.predict_proba({})) == {
        "home_win_prob",
        "draw_prob",
        "away_win_prob",
    }


def test_load_ragged_artifact_is_unfitted(tmp_path: Path) -> None:
    artifact = tmp_path / "bad_stacking_meta_learner.json"
    artifact.write_text(
        json.dumps(
            {
                "coef": [[0.0], [0.0] * FEATURE_COUNT, [0.0] * (FEATURE_COUNT - 1)],
                "intercept": [0.0, 0.0, 0.0],
                "classes": [0, 1, 2],
                "is_fitted": True,
                "training_sample_count": 50,
            }
        ),
        encoding="utf-8",
    )

    learner = StackingMetaLearner()
    learner.load(str(artifact))

    assert learner.is_fitted is False


def test_predict_proba_returns_complete_normalized_three_way_output() -> None:
    learner = StackingMetaLearner()
    learner._coef = [[0.0] * FEATURE_COUNT for _ in range(3)]
    learner._intercept = [0.0, 0.0, 0.0]
    learner._classes = [0, 1, 2]
    learner.is_fitted = True

    probs = learner.predict_proba({})

    assert set(probs) == {"home_win_prob", "draw_prob", "away_win_prob"}
    assert sum(probs.values()) == pytest.approx(1.0)
    assert probs["draw_prob"] == pytest.approx(1.0 / 3.0)
