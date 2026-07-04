from __future__ import annotations

import pytest

from app.services.snapshot_store import (
    _build_prediction_run_feature_snapshot,
    _build_snapshot_pipeline_params,
    _extract_market_probs,
    _normalize_prediction_result,
    _require_match_id,
)


def test_normalize_prediction_result_accepts_canonical_keys():
    result = {
        "meta": {"match_id": "12345678123456781234567812345678"},
        "prediction": {"top_scores": [{"score": "1-0", "prob": 0.12}]},
        "elo": {"elo_gap": 42.0, "detail": {"k_factor": 30.0}},
        "missing_inputs": ["weather"],
    }

    normalized = _normalize_prediction_result(result)

    assert normalized["prediction"]["top3_scores"] == [{"score": "1-0", "prob": 0.12}]
    assert normalized["elo"]["rating_gap"] == 42.0
    assert normalized["elo"]["k_factor"] == 30.0
    assert normalized["missing_inputs"] == ["weather"]


def test_normalize_prediction_result_accepts_legacy_missing_data():
    result = {
        "prediction": {"top3_scores": []},
        "elo": {"rating_gap": 0.0, "k_factor": 20.0},
        "missing_data": [{"item": "lineup"}, "odds"],
    }

    normalized = _normalize_prediction_result(result)

    assert normalized["missing_inputs"] == ["lineup", "odds"]


def test_require_match_id_rejects_empty_or_non_uuid_values():
    with pytest.raises(ValueError):
        _require_match_id("")
    with pytest.raises(ValueError):
        _require_match_id("not-a-match")


def test_require_match_id_accepts_dashed_and_compact_uuid_values():
    compact = "12345678123456781234567812345678"
    dashed = "12345678-1234-5678-1234-567812345678"
    assert _require_match_id(compact) == compact
    assert _require_match_id(dashed) == dashed


def test_normalize_prediction_result_adds_evaluation_sample():
    result = {
        "meta": {
            "match_id": "12345678123456781234567812345678",
            "home_team": "France",
            "away_team": "Brazil",
            "competition": "FIFA World Cup 2026",
            "model_version": "test",
        },
        "prediction": {
            "home_win_prob": 0.5,
            "draw_prob": 0.25,
            "away_win_prob": 0.25,
            "top3_scores": [],
        },
        "component_probs": {"dc": {"home": 0.4, "draw": 0.3, "away": 0.3}},
        "elo": {"rating_gap": 0.0, "k_factor": 20.0},
    }

    normalized = _normalize_prediction_result(result)

    sample = normalized["evaluation_sample"]
    assert sample["candidate_probs"]["current_fusion"]["home"] == 0.5
    assert sample["candidate_probs"]["dc_only"]["home"] == pytest.approx(0.4)


def test_extract_market_probs_requires_complete_three_way_payload():
    invalid = {
        "meta": {},
        "prediction": {},
        "component_probs": {"market": {"home_prob": 0.4, "draw_prob": None, "away_prob": 0.3}},
    }
    valid = {
        "meta": {},
        "prediction": {},
        "component_probs": {"market": {"home_prob": 0.4, "draw_prob": 0.3, "away_prob": 0.3}},
    }

    assert _extract_market_probs(invalid) is None
    assert _extract_market_probs(valid) == {"home": 0.4, "draw": 0.3, "away": 0.3}


def test_snapshot_and_prediction_run_params_share_evaluation_sample():
    sample = {"schema_version": "v1", "candidate_probs": {"uniform_baseline": {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}}}
    pipeline_params = _build_snapshot_pipeline_params(
        {
            "training_rows": 20,
            "pre_market_probs": {"home": 0.4, "draw": 0.3, "away": 0.3},
            "market_weight_used": 0.22,
        },
        {
            "training_rows": 10,
            "weight_config": {"dc": 0.68, "elo": 0.12, "pi": 0.17},
        },
        {"market_weight_used": 0.2, "negbin_applied": True},
        sample,
    )
    feature_snapshot = _build_prediction_run_feature_snapshot(
        {
            "meta": {
                "home_team": "France",
                "away_team": "Brazil",
                "competition": "FIFA World Cup 2026",
                "is_neutral": True,
                "weight_config": {"dc": 0.68, "elo": 0.12, "pi": 0.17},
            },
            "prediction": {"market_weight_used": 0.2},
            "pipeline_params": {
                "training_rows": 20,
                "pre_market_probs": {"home": 0.4, "draw": 0.3, "away": 0.3},
                "market_weight_used": 0.22,
                "calibration_applied": True,
            },
        },
        [],
        sample,
    )

    assert pipeline_params["evaluation_sample"] is sample
    assert pipeline_params["weight_config"]["dc"] == 0.68
    assert pipeline_params["pre_market_probs"]["home"] == 0.4
    assert pipeline_params["market_weight_used"] == 0.22
    assert pipeline_params["negbin_weight"] == 0.05
    assert feature_snapshot["evaluation_sample"] is sample
    assert feature_snapshot["training_rows"] == 20
    assert feature_snapshot["weight_config"]["pi"] == 0.17
    assert feature_snapshot["market_weight_used"] == 0.22
    assert feature_snapshot["calibration_applied"] is True
