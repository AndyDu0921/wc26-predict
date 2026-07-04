"""Tests for conservative weight-proposal gating."""

from __future__ import annotations

from app.services.backtest_gate import BacktestGate, WeightProposalCandidate


BASE = {
    "dc": 0.68,
    "elo": 0.12,
    "pi": 0.17,
    "weibull": 0.10,
    "market_max": 0.30,
}


def _proposal(**overrides) -> WeightProposalCandidate:
    data = {
        "competition": "FIFA World Cup 2026",
        "stage": "group",
        "base_weights": BASE,
        "candidate_weights": {
            "dc": 0.67,
            "elo": 0.13,
            "pi": 0.17,
            "weibull": 0.10,
            "market_max": 0.30,
        },
        "metrics": {
            "current_brier": 0.610,
            "candidate_brier": 0.607,
            "current_logloss": 1.020,
            "candidate_logloss": 1.019,
            "current_rps": 0.290,
            "candidate_rps": 0.289,
        },
        "sample_count": 40,
        "fold_count": 3,
    }
    data.update(overrides)
    return WeightProposalCandidate(**data)


def test_gate_passes_small_weight_change_with_paired_metric_gain():
    decision = BacktestGate().evaluate(_proposal())

    assert decision.passed is True
    assert decision.status == "gate_passed"
    assert decision.metric_deltas["brier"] < 0


def test_gate_rejects_missing_paired_metric():
    proposal = _proposal(metrics={"current_brier": 0.61, "candidate_brier": 0.60})

    decision = BacktestGate().evaluate(proposal)

    assert decision.passed is False
    assert any("missing paired metric: logloss" in item for item in decision.reasons)
    assert any("missing paired metric: rps" in item for item in decision.reasons)


def test_gate_rejects_low_sample_count():
    decision = BacktestGate(min_sample_count=50).evaluate(_proposal(sample_count=40))

    assert decision.passed is False
    assert any("sample_count" in item for item in decision.reasons)


def test_gate_rejects_low_fold_count():
    decision = BacktestGate(min_fold_count=3).evaluate(_proposal(fold_count=2))

    assert decision.passed is False
    assert any("fold_count" in item for item in decision.reasons)


def test_gate_rejects_large_weight_delta():
    decision = BacktestGate(max_weight_delta=0.03).evaluate(
        _proposal(
            candidate_weights={
                **BASE,
                "pi": 0.23,
            }
        )
    )

    assert decision.passed is False
    assert any("pi delta" in item for item in decision.reasons)


def test_gate_rejects_metric_worsening_even_when_other_metric_improves():
    decision = BacktestGate().evaluate(
        _proposal(
            metrics={
                "current_brier": 0.610,
                "candidate_brier": 0.606,
                "current_logloss": 1.020,
                "candidate_logloss": 1.030,
                "current_rps": 0.290,
                "candidate_rps": 0.289,
            }
        )
    )

    assert decision.passed is False
    assert any("logloss worsened" in item for item in decision.reasons)


def test_gate_does_not_accept_ece_only_improvement():
    decision = BacktestGate().evaluate(
        _proposal(
            metrics={
                "current_brier": 0.610,
                "candidate_brier": 0.610,
                "current_logloss": 1.020,
                "candidate_logloss": 1.020,
                "current_rps": 0.290,
                "candidate_rps": 0.290,
                "current_ece": 0.080,
                "candidate_ece": 0.060,
            }
        )
    )

    assert decision.passed is False
    assert any("no core scoring metric improved" in item for item in decision.reasons)
