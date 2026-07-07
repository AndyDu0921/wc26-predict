import pytest

from app.services.model_candidates import get_shadow_candidate_model, list_shadow_candidate_models


def test_all_model_candidates_are_shadow_only():
    candidates = list_shadow_candidate_models()

    assert candidates
    assert all(item["shadow_only"] is True for item in candidates)
    assert all(item["production_enabled"] is False for item in candidates)
    assert all("brier" in item["evaluation_metrics"] for item in candidates)
    assert all("logloss" in item["evaluation_metrics"] for item in candidates)
    assert all("rps" in item["evaluation_metrics"] for item in candidates)


def test_dynamic_bivariate_poisson_candidate_is_registered():
    spec = get_shadow_candidate_model("dynamic_bivariate_poisson")

    assert spec["family"] == "dynamic_bivariate_poisson"
    assert spec["rollout_gate"] == "paired_walk_forward_backtest_gate"


def test_unknown_model_candidate_raises():
    with pytest.raises(KeyError):
        get_shadow_candidate_model("production_magic_model")
