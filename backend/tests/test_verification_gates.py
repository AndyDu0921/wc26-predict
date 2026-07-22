"""Unit tests for verification_gates.py — P0-1 snapshot completeness gate."""
from __future__ import annotations

from app.core.verification_gates import (
    verify_snapshot_completeness,
    REQUIRED_COMPONENT_KEYS,
    GateResult,
    preflight_check,
    postflight_check,
    postmatch_check,
    format_gate_results,
    all_errors_passed,
)


class TestVerifySnapshotCompleteness:
    """P0-1: verify_snapshot_completeness() function tests."""

    # ── Complete snapshot ──

    def test_all_components_present_is_complete(self):
        """Full snapshot with all 7 components + xG + market = complete."""
        component_probs = {
            "dixon_coles": {"home": 0.45, "draw": 0.25, "away": 0.30},
            "enhancer": {"home": 0.48, "draw": 0.22, "away": 0.30},
            "negbin": {"home": 0.44, "draw": 0.26, "away": 0.30},
            "weibull": {"home": 0.50, "draw": 0.20, "away": 0.30},
            "elo": {"home": 0.42, "draw": 0.28, "away": 0.30},
            "pi_rating": {"home": 0.47, "draw": 0.23, "away": 0.30},
            "market": {"home": 0.46, "draw": 0.24, "away": 0.30},
        }
        is_complete, missing, gates = verify_snapshot_completeness(
            component_probs=component_probs,
            home_xg=1.5,
            away_xg=0.8,
            market_blended=True,
            market_weight_used=0.30,
            market_divergence=0.05,
        )
        assert is_complete
        assert missing == []
        assert all(g.passed for g in gates)

    # ── Missing components ──

    def test_missing_single_component(self):
        """6/7 components = incomplete."""
        component_probs = {
            "dixon_coles": {"home": 0.45, "draw": 0.25, "away": 0.30},
            "enhancer": {"home": 0.48, "draw": 0.22, "away": 0.30},
            "negbin": {"home": 0.44, "draw": 0.26, "away": 0.30},
            "weibull": {"home": 0.50, "draw": 0.20, "away": 0.30},
            "elo": {"home": 0.42, "draw": 0.28, "away": 0.30},
            "pi_rating": {"home": 0.47, "draw": 0.23, "away": 0.30},
            # market missing
        }
        is_complete, missing, gates = verify_snapshot_completeness(
            component_probs=component_probs,
            home_xg=1.5, away_xg=0.8,
        )
        assert not is_complete
        assert "component_probs.market" in missing
        # Should have at least one error gate
        errors = [g for g in gates if g.severity == "error"]
        assert len(errors) >= 1

    def test_missing_multiple_components(self):
        """4/7 components = incomplete with multiple missing."""
        component_probs = {
            "dixon_coles": {"home": 0.45, "draw": 0.25, "away": 0.30},
            "elo": {"home": 0.42, "draw": 0.28, "away": 0.30},
            "pi_rating": {"home": 0.47, "draw": 0.23, "away": 0.30},
            "market": {"home": 0.46, "draw": 0.24, "away": 0.30},
        }
        is_complete, missing, gates = verify_snapshot_completeness(
            component_probs=component_probs,
            home_xg=1.5, away_xg=0.8,
        )
        assert not is_complete
        assert len(missing) >= 3  # enhancer, negbin, weibull
        assert "component_probs.enhancer" in missing
        assert "component_probs.negbin" in missing
        assert "component_probs.weibull" in missing

    def test_none_component_probs(self):
        """component_probs=None → critical failure."""
        is_complete, missing, gates = verify_snapshot_completeness(
            component_probs=None,
            home_xg=1.5, away_xg=0.8,
        )
        assert not is_complete
        assert any("component_probs" in m for m in missing)
        errors = [g for g in gates if g.severity == "error"]
        assert len(errors) >= 1

    def test_empty_component_probs(self):
        """Empty dict → all 7 components + 3 market metadata missing."""
        is_complete, missing, gates = verify_snapshot_completeness(
            component_probs={},
            home_xg=1.5, away_xg=0.8,
        )
        assert not is_complete
        # 7 components + 3 market metadata fields (not passed) = 10
        assert len(missing) == 10
        for key in REQUIRED_COMPONENT_KEYS:
            assert f"component_probs.{key}" in missing
        assert "market_blended" in missing
        assert "market_weight_used" in missing
        assert "market_divergence" in missing

    # ── Missing xG ──

    def test_missing_home_xg(self):
        """home_xg=None → warning, not error."""
        component_probs = {k: {"home": 0.45, "draw": 0.25, "away": 0.30}
                          for k in REQUIRED_COMPONENT_KEYS}
        is_complete, missing, gates = verify_snapshot_completeness(
            component_probs=component_probs,
            home_xg=None,
            away_xg=0.8,
            market_blended=True, market_weight_used=0.30, market_divergence=0.05,
        )
        assert not is_complete
        assert "home_xg" in missing
        assert "away_xg" not in missing
        # xG missing is a warning, not error
        xg_gates = [g for g in gates if g.gate == "snapshot_xg"]
        assert xg_gates[0].severity == "warning"

    def test_missing_away_xg(self):
        """away_xg=None → warning."""
        component_probs = {k: {"home": 0.45, "draw": 0.25, "away": 0.30}
                          for k in REQUIRED_COMPONENT_KEYS}
        is_complete, missing, gates = verify_snapshot_completeness(
            component_probs=component_probs,
            home_xg=1.5,
            away_xg=None,
            market_blended=True, market_weight_used=0.30, market_divergence=0.05,
        )
        assert not is_complete
        assert "away_xg" in missing

    def test_missing_both_xg(self):
        """Both xG None → both in missing."""
        component_probs = {k: {"home": 0.45, "draw": 0.25, "away": 0.30}
                          for k in REQUIRED_COMPONENT_KEYS}
        is_complete, missing, gates = verify_snapshot_completeness(
            component_probs=component_probs,
            home_xg=None,
            away_xg=None,
            market_blended=True, market_weight_used=0.30, market_divergence=0.05,
        )
        assert not is_complete
        assert "home_xg" in missing
        assert "away_xg" in missing

    # ── Missing market metadata ──

    def test_missing_market_blended(self):
        """market_blended=None → warning."""
        component_probs = {k: {"home": 0.45, "draw": 0.25, "away": 0.30}
                          for k in REQUIRED_COMPONENT_KEYS}
        is_complete, missing, gates = verify_snapshot_completeness(
            component_probs=component_probs,
            home_xg=1.5, away_xg=0.8,
            market_blended=None,
            market_weight_used=0.30, market_divergence=0.05,
        )
        assert not is_complete
        assert "market_blended" in missing

    def test_missing_market_weight(self):
        """market_weight_used=None → warning."""
        component_probs = {k: {"home": 0.45, "draw": 0.25, "away": 0.30}
                          for k in REQUIRED_COMPONENT_KEYS}
        is_complete, missing, gates = verify_snapshot_completeness(
            component_probs=component_probs,
            home_xg=1.5, away_xg=0.8,
            market_blended=True,
            market_weight_used=None, market_divergence=0.05,
        )
        assert not is_complete
        assert "market_weight_used" in missing

    def test_missing_all_market_metadata(self):
        """All 3 market fields None → all in missing."""
        component_probs = {k: {"home": 0.45, "draw": 0.25, "away": 0.30}
                          for k in REQUIRED_COMPONENT_KEYS}
        is_complete, missing, gates = verify_snapshot_completeness(
            component_probs=component_probs,
            home_xg=1.5, away_xg=0.8,
            market_blended=None,
            market_weight_used=None,
            market_divergence=None,
        )
        assert not is_complete
        assert "market_blended" in missing
        assert "market_weight_used" in missing
        assert "market_divergence" in missing

    # ── Component count check ──

    def test_low_component_count(self):
        """component_count < 5 → error added."""
        is_complete, missing, gates = verify_snapshot_completeness(
            component_probs={"dixon_coles": {}, "elo": {}, "pi_rating": {}, "market": {}},
            home_xg=1.5, away_xg=0.8,
            market_blended=True, market_weight_used=0.30, market_divergence=0.05,
            component_count=4,
        )
        assert not is_complete
        count_errors = [g for g in gates if g.gate == "snapshot_component_count"]
        assert len(count_errors) == 1
        assert count_errors[0].severity == "error"

    def test_sufficient_component_count(self):
        """component_count >= 5 → no component_count gate."""
        _, _, gates = verify_snapshot_completeness(
            component_probs={k: {} for k in REQUIRED_COMPONENT_KEYS},
            home_xg=1.5, away_xg=0.8,
            market_blended=True, market_weight_used=0.30, market_divergence=0.05,
            component_count=7,
        )
        count_gates = [g for g in gates if g.gate == "snapshot_component_count"]
        assert len(count_gates) == 0

    # ── All defaults = incomplete ──

    def test_no_args_returns_incomplete(self):
        """Call with no arguments → incomplete (no data at all)."""
        is_complete, missing, gates = verify_snapshot_completeness()
        assert not is_complete
        assert len(missing) > 0


class TestPreflightCheck:
    """Smoke tests for preflight_check."""

    def test_all_defaults_warn(self):
        """All defaults → some warnings."""
        warnings = preflight_check()
        assert len(warnings) >= 1  # venue not confirmed

    def test_elo_default_warns(self):
        """Elo=1500 for both teams should warn."""
        warnings = preflight_check(home_elo=1500.0, away_elo=1500.0)
        elo_warnings = [w for w in warnings if w.gate == "elo_default_check"]
        assert len(elo_warnings) == 1
        assert not elo_warnings[0].passed

    def test_elo_valid_passes(self):
        """Non-default Elo passes."""
        warnings = preflight_check(home_elo=1684.0, away_elo=1550.0)
        elo_warnings = [w for w in warnings if w.gate == "elo_default_check"]
        assert len(elo_warnings) == 0  # Not in results at all

    def test_venue_not_confirmed_warns(self):
        """venue_confirmed=False → warning."""
        warnings = preflight_check(venue_confirmed=False)
        venue_warnings = [w for w in warnings if w.gate == "venue_confirmed"]
        assert len(venue_warnings) == 1

    def test_wc_competition_weight_too_low_errors(self):
        """WC with weight < 1.0 → error."""
        warnings = preflight_check(
            competition_weight=0.5,
            competition_type="FIFA World Cup 2026",
            match_stage="Group Stage",
        )
        cw = [w for w in warnings if w.gate == "competition_weight"]
        assert len(cw) == 1
        assert cw[0].severity == "error"


class TestPostflightCheck:
    """Smoke tests for postflight_check."""

    def test_all_good_passes(self):
        """Good data → no failures."""
        failures = postflight_check(
            probs={"home_win_prob": 0.45, "draw_prob": 0.25, "away_win_prob": 0.30},
            all_components_run=7,
            market_applied=True,
            market_provider_count=5,
            calibration_applied=True,
        )
        assert len(failures) == 0

    def test_only_5_components_warns(self):
        """5 components → warning about missing 2."""
        failures = postflight_check(
            probs={"home_win_prob": 0.45, "draw_prob": 0.25, "away_win_prob": 0.30},
            all_components_run=5,
            market_applied=True,
            market_provider_count=5,
            calibration_applied=True,
        )
        comp_failures = [f for f in failures if f.gate == "all_components_run"]
        assert len(comp_failures) == 1
        assert comp_failures[0].severity == "warning"

    def test_extreme_prob_error(self):
        """0.0 probability → error."""
        failures = postflight_check(
            probs={"home_win_prob": 0.00, "draw_prob": 0.50, "away_win_prob": 0.50},
            all_components_run=7,
            market_applied=True,
            market_provider_count=5,
            calibration_applied=True,
        )
        assert len(failures) >= 1

    def test_minimum_probability_clip_boundary_is_valid(self):
        failures = postflight_check(
            probs={"home_win_prob": 0.02, "draw_prob": 0.48, "away_win_prob": 0.50},
            all_components_run=7,
            market_required=False,
            calibration_applied=True,
        )

        assert not [failure for failure in failures if failure.gate == "no_extreme_probs"]

    def test_explicit_debug_run_can_disable_market_checks(self):
        failures = postflight_check(
            probs={"home_win_prob": 0.45, "draw_prob": 0.25, "away_win_prob": 0.30},
            all_components_run=7,
            market_applied=False,
            market_provider_count=0,
            market_required=False,
            calibration_applied=True,
        )

        assert not [failure for failure in failures if failure.gate.startswith("market")]

    def test_calibration_not_applied_errors(self):
        """calibration_applied=False → error."""
        failures = postflight_check(
            probs={"home_win_prob": 0.45, "draw_prob": 0.25, "away_win_prob": 0.30},
            all_components_run=7,
            market_applied=True,
            market_provider_count=5,
            calibration_applied=False,
        )
        cal_failures = [f for f in failures if f.gate == "calibration_applied"]
        assert len(cal_failures) == 1
        assert cal_failures[0].severity == "error"

    def test_probs_dont_sum_to_one(self):
        """Probabilities summing to 0.9 → error."""
        failures = postflight_check(
            probs={"home_win_prob": 0.30, "draw_prob": 0.30, "away_win_prob": 0.30},
            all_components_run=7,
            market_applied=True,
            market_provider_count=5,
            calibration_applied=True,
        )
        sum_failures = [f for f in failures if f.gate == "probs_sum_to_one"]
        assert len(sum_failures) == 1
        assert sum_failures[0].severity == "error"

    def test_ko_draw_underestimation_close_elo(self):
        """KO match with close Elo but low draw → warning."""
        failures = postflight_check(
            probs={"home_win_prob": 0.55, "draw_prob": 0.18, "away_win_prob": 0.27},
            all_components_run=7,
            market_applied=True,
            market_provider_count=5,
            calibration_applied=True,
            is_knockout=True,
            elo_gap=30,
        )
        ko_failures = [f for f in failures if f.gate == "ko_draw_underestimation"]
        assert len(ko_failures) == 1

    def test_market_applied_false_warns(self):
        """market_applied=False → warning."""
        failures = postflight_check(
            probs={"home_win_prob": 0.45, "draw_prob": 0.25, "away_win_prob": 0.30},
            all_components_run=7,
            market_applied=False,
            market_provider_count=5,
            calibration_applied=True,
        )
        market = [f for f in failures if f.gate == "market_applied"]
        assert len(market) == 1


class TestPostmatchCheck:
    """Smoke tests for postmatch_check."""

    def test_insufficient_sources_errors(self):
        """0 sources → error."""
        failures = postmatch_check(score_sources=0)
        src = [f for f in failures if f.gate == "score_sources"]
        assert len(src) == 1
        assert src[0].severity == "error"

    def test_snapshot_missing_errors(self):
        """snapshot_exists=False → error."""
        failures = postmatch_check(
            score_sources=2,
            snapshot_exists=False,
            snapshot_is_complete=True,
        )
        snap = [f for f in failures if f.gate == "snapshot_exists"]
        assert len(snap) == 1
        assert snap[0].severity == "error"

    def test_incomplete_snapshot_warns(self):
        """snapshot_is_complete=False → warning."""
        failures = postmatch_check(
            score_sources=2,
            snapshot_exists=True,
            snapshot_is_complete=False,
        )
        comp = [f for f in failures if f.gate == "snapshot_complete"]
        assert len(comp) == 1
        assert comp[0].severity == "warning"

    def test_all_good_passes_everything(self):
        """All preconditions met → no failures."""
        failures = postmatch_check(
            score_sources=2,
            snapshot_exists=True,
            snapshot_is_complete=True,
            previous_learning_log_conflict=False,
        )
        assert len(failures) == 0


class TestFormatGateResults:
    """Output formatting tests."""

    def test_empty_results(self):
        output = format_gate_results([], "Test")
        assert "✅ All checks passed." in output

    def test_with_errors(self):
        results = [
            GateResult(gate="test", passed=False, severity="error",
                       message="Something broke"),
        ]
        output = format_gate_results(results, "Test")
        assert "❌ Errors" in output
        assert "Something broke" in output

    def test_with_warnings(self):
        results = [
            GateResult(gate="test", passed=False, severity="warning",
                       message="Heads up"),
        ]
        output = format_gate_results(results, "Test")
        assert "⚠️  Warnings" in output
        assert "Heads up" in output


class TestAllErrorsPassed:
    """Gate pass/fail aggregation."""

    def test_no_errors_passes(self):
        results = [
            GateResult(gate="a", passed=False, severity="warning", message="warn"),
            GateResult(gate="b", passed=True, severity="info", message="ok"),
        ]
        assert all_errors_passed(results)

    def test_with_errors_fails(self):
        results = [
            GateResult(gate="a", passed=False, severity="error", message="broken"),
        ]
        assert not all_errors_passed(results)

    def test_empty_list_passes(self):
        assert all_errors_passed([])
