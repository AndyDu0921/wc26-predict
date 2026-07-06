from __future__ import annotations

import pytest

from app.services.snapshot_store import (
    _build_prediction_run_feature_snapshot,
    _build_snapshot_pipeline_params,
    _collect_approved_signal_payloads,
    _extract_market_probs,
    _json_dumps,
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


def test_collect_approved_signal_payloads_normalizes_structured_signals_and_ids():
    result = {
        "approved_signals": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "team_name": "Portugal",
                "signal_type": "return",
                "impact_direction": "positive",
                "confidence": 0.82,
                "source_reliability": 0.74,
                "summary_zh": "关键前锋恢复合练",
                "key_players": "Forward A",
            }
        ],
        "active_event_ids": [
            "22222222-2222-2222-2222-222222222222",
            "11111111-1111-1111-1111-111111111111",
        ],
    }
    adjustment_log = [{"signal_id": "33333333-3333-3333-3333-333333333333"}]

    signals = _collect_approved_signal_payloads(result, adjustment_log)

    assert [item["id"] for item in signals] == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "33333333-3333-3333-3333-333333333333",
    ]
    assert signals[0]["team"] == "Portugal"
    assert signals[0]["key_players"] == ["Forward A"]
    assert signals[1]["signal_type"] == "unknown"
    assert signals[1]["impact_direction"] == "neutral"


def test_prediction_run_feature_snapshot_keeps_approved_signal_ids():
    feature_snapshot = _build_prediction_run_feature_snapshot(
        {
            "meta": {
                "home_team": "Portugal",
                "away_team": "Spain",
                "competition": "FIFA World Cup 2026",
            },
            "prediction": {},
        },
        [{"signal_id": "11111111-1111-1111-1111-111111111111"}],
        {"schema_version": "v1"},
        [
            {"id": "11111111-1111-1111-1111-111111111111"},
            {"id": "22222222-2222-2222-2222-222222222222"},
        ],
    )

    assert feature_snapshot["approved_signal_ids"] == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ]


def test_prediction_run_json_payloads_preserve_utf8_risk_tags_and_signals():
    import json

    payload = {
        "risk_tags": ["主队有利情报", "市场分歧"],
        "approved_signals": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "signal_type": "return",
                "impact_direction": "positive",
                "summary_zh": "关键球员复出",
                "source_reliability": 0.8,
                "confidence": 0.7,
                "key_players": ["核心前锋"],
            }
        ],
    }

    dumped = _json_dumps(payload)
    loaded = json.loads(dumped)

    assert loaded == payload
    assert "主队有利情报" in dumped


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
