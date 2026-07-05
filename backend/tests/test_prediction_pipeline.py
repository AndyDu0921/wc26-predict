"""Tests for PredictionPipeline, RunQuality, and degraded_reasons contract.

Ticket 1.2: prediction_pipeline contract strengthening.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.prediction_pipeline import (
    PredictionPipeline,
    _build_stacking_component_probs,
    _resolve_weibull_scenario_action,
)
from app.services.prediction_result import DegradedReason, PredictionResult, SourceStatus
from app.services.run_quality import RunQuality
from app.services.evaluation_sample import EVALUATION_CANDIDATE_LABELS


class TestPredictionPipeline:
    """Verify PredictionPipeline class structure."""

    def test_prediction_pipeline_has_predict_match(self) -> None:
        assert hasattr(PredictionPipeline, "predict_match")

    def test_prediction_pipeline_has_predict_alias(self) -> None:
        assert hasattr(PredictionPipeline, "predict")

    def test_predict_is_async_callable(self) -> None:
        assert asyncio.iscoroutinefunction(PredictionPipeline.predict)


class TestPredictionPipelineHelpers:
    """Verify shared helpers used by async/sync prediction paths."""

    def test_weibull_scenario_shadows_extreme_ko_draw_instead_of_skip(self) -> None:
        """V4.8.1: KO extreme draw is now 'shadow' (reduced weight) not 'skip' (zero).
        KO draw rate is 25% (4/16) — Weibull draw signals should be preserved as reference."""
        action, weight = _resolve_weibull_scenario_action(
            weibull_probs={
                "home_win_prob": 0.25,
                "draw_prob": 0.45,
                "away_win_prob": 0.30,
            },
            base_weight=0.10,
            elo_gap=12.0,
            stage="Round of 16",
            market_probs=None,
            total_xg=2.4,
        )

        assert action["action"] == "shadow"
        assert action["scenario"] == "draw_noise"
        assert weight == 0.05  # min(0.10 * 0.50, 0.05) = 0.05

    def test_build_stacking_component_probs_uses_canonical_component_keys(self) -> None:
        components = _build_stacking_component_probs(
            dc_pred={"home_win_prob": 0.50, "draw_prob": 0.30, "away_win_prob": 0.20},
            enhancer_pred={"home_win_prob": 0.48, "draw_prob": 0.28, "away_win_prob": 0.24},
            elo_pred=SimpleNamespace(home_win_prob=0.45, draw_prob=0.30, away_win_prob=0.25),
            pi_pred={"home_win_prob": 0.43, "draw_prob": 0.31, "away_win_prob": 0.26},
            weibull_pred={"home_win_prob": 0.47, "draw_prob": 0.29, "away_win_prob": 0.24},
            negbin_probs={"home": 0.46, "draw": 0.32, "away": 0.22},
            market_probs={"home_prob": 0.44, "draw_prob": 0.33, "away_prob": 0.23},
        )

        assert set(components) == {
            "dixon_coles",
            "enhancer",
            "negbin",
            "weibull",
            "elo",
            "pi_rating",
            "market",
        }
        assert components["market"]["draw"] == pytest.approx(0.33)
        assert sum(components["negbin"].values()) == pytest.approx(1.0)


class TestEvaluationSampleContract:
    """Verify V3.5.4 evaluation_sample output."""

    def test_to_dict_includes_stable_evaluation_sample(self) -> None:
        result = PredictionResult(
            home_team="Argentina",
            away_team="Brazil",
            competition="FIFA World Cup 2026",
            match_id="12345678-1234-5678-1234-567812345678",
            home_win_prob=0.50,
            draw_prob=0.25,
            away_win_prob=0.25,
            dc_probs={"home": 0.45, "draw": 0.30, "away": 0.25},
            enhancer_probs={"home": 0.48, "draw": 0.27, "away": 0.25},
            elo_probs={"home": 0.40, "draw": 0.30, "away": 0.30},
            pi_probs={"home": 0.42, "draw": 0.29, "away": 0.29},
            weibull_probs={"home": 0.46, "draw": 0.28, "away": 0.26},
            market_probs={"home_prob": 0.44, "draw_prob": 0.31, "away_prob": 0.25},
            calibration_monitor={"baseline_probs": {"home": 0.47, "draw": 0.28, "away": 0.25}},
            as_of="2026-06-01T00:00:00Z",
            generated_at="2026-06-01T00:00:01Z",
        )

        sample = result.to_dict()["evaluation_sample"]

        assert sample["schema_version"] == "v1"
        assert sample["match_id"] == "12345678-1234-5678-1234-567812345678"
        assert set(sample["candidate_status"]) == set(EVALUATION_CANDIDATE_LABELS)
        assert sample["candidate_probs"]["current_fusion"] == {"home": 0.5, "draw": 0.25, "away": 0.25}
        assert sample["candidate_probs"]["snapshot_baseline"]["home"] == pytest.approx(0.47)
        assert sample["candidate_probs"]["market_only"]["draw"] == pytest.approx(0.31)

    def test_invalid_market_probs_are_marked_missing(self) -> None:
        result = PredictionResult(
            home_team="Argentina",
            away_team="Brazil",
            competition="FIFA World Cup 2026",
            market_probs={"home_prob": 0.44, "draw_prob": None, "away_prob": 0.25},
        )

        sample = result.to_dict()["evaluation_sample"]

        assert "market_only" not in sample["candidate_probs"]
        assert sample["candidate_status"]["market_only"]["status"] == "missing"


class TestRunQuality:
    """Verify RunQuality dataclass behaviour."""

    def test_run_quality_creation(self) -> None:
        rq = RunQuality()
        assert rq.pipeline_status == "full"
        assert rq.model_components["dixon_coles"] == "skipped"
        assert rq.cache["dixon_coles"] == "miss"
        assert rq.fact_check == "skipped"
        assert rq.warnings == []

    def test_run_quality_degraded(self) -> None:
        rq = RunQuality()
        rq.mark_degraded("DC fit failed, using cached values")
        assert rq.pipeline_status == "degraded"
        assert "DC fit failed, using cached values" in rq.warnings

    def test_run_quality_to_dict(self) -> None:
        rq = RunQuality()
        rq.mark_degraded("No training data for competition")
        d = rq.to_dict()
        assert d["pipeline_status"] == "degraded"
        assert len(d["warnings"]) == 1
        assert d["warnings"][0] == "No training data for competition"
        assert isinstance(d["model_components"], dict)


# ═══════════════════════════════════════════════════════════════════════════════
# Ticket 1.2: degraded_reasons contract tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestDegradedReasonDataclass:
    """Verify DegradedReason structure and serialization."""

    def test_required_fields(self) -> None:
        dr = DegradedReason(
            source="pi_rating",
            reason="fitting_failed",
            severity="warning",
            detail="ZeroDivisionError",
        )
        assert dr.source == "pi_rating"
        assert dr.reason == "fitting_failed"
        assert dr.severity == "warning"
        assert dr.detail == "ZeroDivisionError"

    def test_default_severity_is_warning(self) -> None:
        dr = DegradedReason(source="weather", reason="api_timeout")
        assert dr.severity == "warning"

    def test_default_detail_is_empty(self) -> None:
        dr = DegradedReason(source="weather", reason="api_timeout")
        assert dr.detail == ""

    def test_to_dict_roundtrip(self) -> None:
        original = DegradedReason(
            source="market_calibration",
            reason="calibration_failed",
            severity="error",
            detail="Connection refused",
        )
        d = original.to_dict()
        restored = DegradedReason.from_dict(d)
        assert restored.source == original.source
        assert restored.reason == original.reason
        assert restored.severity == original.severity
        assert restored.detail == original.detail


class TestSourceStatusDataclass:
    """Verify SourceStatus structure and serialization."""

    def test_to_dict_roundtrip(self) -> None:
        original = SourceStatus(
            status="used",
            reason="forecast_loaded",
            detail="clear",
            attempted=True,
            required=True,
        )
        restored = SourceStatus.from_dict(original.to_dict())

        assert restored.status == "used"
        assert restored.reason == "forecast_loaded"
        assert restored.detail == "clear"
        assert restored.attempted is True
        assert restored.required is True

    def test_invalid_status_falls_back_to_skipped(self) -> None:
        restored = SourceStatus.from_dict({"status": "unknown"})
        assert restored.status == "skipped"


class TestPredictionResultDegradedReasons:
    """Verify PredictionResult.degraded_reasons field contract."""

    def test_normal_prediction_has_empty_degraded_reasons(self) -> None:
        """Normal prediction (no failures) must have empty degraded_reasons."""
        result = PredictionResult(
            home_team="Argentina",
            away_team="Brazil",
            competition="FIFA World Cup 2026",
        )
        assert result.degraded_reasons == []
        assert isinstance(result.degraded_reasons, list)

    def test_one_source_missing(self) -> None:
        """Single data source failure must be recorded."""
        result = PredictionResult(
            home_team="Argentina",
            away_team="Brazil",
            competition="FIFA World Cup 2026",
            degraded_reasons=[
                DegradedReason(
                    source="pi_rating",
                    reason="fitting_failed",
                    severity="warning",
                    detail="ValueError: singular matrix",
                ),
            ],
        )
        assert len(result.degraded_reasons) == 1
        dr = result.degraded_reasons[0]
        assert dr.source == "pi_rating"
        assert dr.reason == "fitting_failed"
        assert dr.severity == "warning"

    def test_multiple_sources_missing(self) -> None:
        """Multiple data source failures must all be recorded."""
        result = PredictionResult(
            home_team="Argentina",
            away_team="Brazil",
            competition="FIFA World Cup 2026",
            degraded_reasons=[
                DegradedReason(
                    source="pi_rating",
                    reason="fitting_failed",
                    severity="warning",
                    detail="ValueError",
                ),
                DegradedReason(
                    source="market_calibration",
                    reason="api_unavailable",
                    severity="error",
                    detail="Connection timeout",
                ),
                DegradedReason(
                    source="context_adjuster",
                    reason="db_query_failed",
                    severity="warning",
                    detail="OperationalError",
                ),
            ],
        )
        assert len(result.degraded_reasons) == 3
        sources = {dr.source for dr in result.degraded_reasons}
        assert sources == {"pi_rating", "market_calibration", "context_adjuster"}

    def test_to_dict_includes_degraded_reasons(self) -> None:
        """to_dict() must serialize degraded_reasons."""
        result = PredictionResult(
            home_team="Argentina",
            away_team="Brazil",
            competition="FIFA World Cup 2026",
            degraded_reasons=[
                DegradedReason(
                    source="market_calibration",
                    reason="calibration_failed",
                    severity="warning",
                ),
            ],
        )
        d = result.to_dict()
        assert "degraded_reasons" in d
        assert len(d["degraded_reasons"]) == 1
        assert d["degraded_reasons"][0]["source"] == "market_calibration"
        assert d["degraded_reasons"][0]["reason"] == "calibration_failed"
        assert d["degraded_reasons"][0]["severity"] == "warning"

    def test_from_dict_restores_degraded_reasons(self) -> None:
        """from_dict() must round-trip degraded_reasons."""
        original = PredictionResult(
            home_team="Argentina",
            away_team="Brazil",
            competition="FIFA World Cup 2026",
            degraded_reasons=[
                DegradedReason(
                    source="weather",
                    reason="api_timeout",
                    severity="error",
                    detail="httpx.TimeoutException",
                ),
                DegradedReason(
                    source="pi_rating",
                    reason="fitting_failed",
                    severity="warning",
                ),
            ],
        )
        d = original.to_dict()
        restored = PredictionResult.from_dict(d)
        assert len(restored.degraded_reasons) == 2
        assert restored.degraded_reasons[0].source == "weather"
        assert restored.degraded_reasons[0].severity == "error"
        assert restored.degraded_reasons[1].source == "pi_rating"

    def test_empty_degraded_reasons_survives_roundtrip(self) -> None:
        """No degraded_reasons must round-trip as empty list, not None."""
        result = PredictionResult(
            home_team="Argentina",
            away_team="Brazil",
            competition="FIFA World Cup 2026",
        )
        d = result.to_dict()
        restored = PredictionResult.from_dict(d)
        assert restored.degraded_reasons == []

    def test_source_status_survives_roundtrip(self) -> None:
        result = PredictionResult(
            home_team="Argentina",
            away_team="Brazil",
            competition="FIFA World Cup 2026",
            source_status={
                "weather": SourceStatus(
                    status="unavailable",
                    reason="forecast_unavailable",
                    attempted=True,
                ),
                "market": SourceStatus(
                    status="skipped",
                    reason="disabled_by_flag",
                ),
            },
        )
        restored = PredictionResult.from_dict(result.to_dict())

        assert restored.source_status["weather"].status == "unavailable"
        assert restored.source_status["weather"].attempted is True
        assert restored.source_status["market"].reason == "disabled_by_flag"

    def test_degraded_reasons_structure_is_stable(self) -> None:
        """Every degraded reason must have source, reason, severity keys."""
        result = PredictionResult(
            home_team="Argentina",
            away_team="Brazil",
            competition="FIFA World Cup 2026",
            degraded_reasons=[
                DegradedReason(source="x", reason="y", severity="warning"),
                DegradedReason(source="a", reason="b", severity="error"),
            ],
        )
        d = result.to_dict()
        for dr in d["degraded_reasons"]:
            required = {"source", "reason", "severity"}
            assert required.issubset(set(dr.keys())), f"Missing keys in {dr}"
            assert isinstance(dr["source"], str) and dr["source"], "source must be non-empty str"
            assert isinstance(dr["reason"], str) and dr["reason"], "reason must be non-empty str"
            assert dr["severity"] in ("warning", "error"), f"Invalid severity: {dr['severity']}"

    def test_degraded_reasons_not_lost_on_conversion(self) -> None:
        """Degraded reasons must survive to_dict → from_dict without data loss."""
        result = PredictionResult(
            home_team="France",
            away_team="Ivory Coast",
            competition="International Friendly",
            is_neutral=True,
            degraded_reasons=[
                DegradedReason(
                    source="market_calibration",
                    reason="api_unavailable",
                    severity="warning",
                    detail="No API key configured",
                ),
            ],
        )
        d = result.to_dict()
        restored = PredictionResult.from_dict(d)

        assert restored.home_team == "France"
        assert restored.away_team == "Ivory Coast"
        assert restored.is_neutral is True
        assert len(restored.degraded_reasons) == 1
        dr = restored.degraded_reasons[0]
        assert dr.source == "market_calibration"
        assert dr.reason == "api_unavailable"
        assert dr.detail == "No API key configured"
