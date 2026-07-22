from __future__ import annotations

import pytest

from app.services.learning_engine import _coerce_probs
from app.services.market.probability import normalize_1x2_odds, normalize_1x2_shin
from app.services.postmatch import evaluate_prediction
from app.services.snapshot_service import save_pre_match_snapshot


def test_enhanced_prediction_does_not_return_uniform_defaults_on_core_failure(monkeypatch):
    from app.services import canonical_prediction_core
    from app.services.prediction_enhanced import run_enhanced_prediction

    def fail_core(*_args, **_kwargs):
        raise RuntimeError("artifact unavailable")

    monkeypatch.setattr(canonical_prediction_core, "execute_prediction_core", fail_core)

    with pytest.raises(RuntimeError, match="Base enhanced prediction failed"):
        run_enhanced_prediction(
            "France",
            "Spain",
            enable_market=False,
            enable_weather=False,
            enable_llm=False,
        )


def test_snapshot_requires_explicit_probabilities():
    with pytest.raises(ValueError, match="requires explicit H/D/A"):
        save_pre_match_snapshot(
            home_team="France",
            away_team="Spain",
            competition="FIFA World Cup 2026",
            match_id="205",
        )


def test_postmatch_evaluation_rejects_missing_probabilities():
    with pytest.raises(ValueError, match="requires stored probabilities"):
        evaluate_prediction(
            {
                "home_team": "France",
                "away_team": "Spain",
                "competition": "FIFA World Cup 2026",
            },
            1,
            0,
        )


def test_learning_attribution_rejects_partial_component_probabilities():
    with pytest.raises(ValueError, match="missing probability"):
        _coerce_probs({"home": 0.6, "away": 0.4})


def test_nonconvergent_shin_uses_observed_odds_not_uniform_placeholder():
    proportional = normalize_1x2_odds(1.50, 4.20, 7.00)
    result = normalize_1x2_shin(1.50, 4.20, 7.00, max_iter=0)

    assert result["home"] == pytest.approx(proportional["home"])
    assert result["draw"] == pytest.approx(proportional["draw"])
    assert result["away"] == pytest.approx(proportional["away"])
