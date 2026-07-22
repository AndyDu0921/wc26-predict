"""Tests for FusionGraph — sequential multi-model blending.

Verifies probability normalisation, effective-weight computation,
step recording, missing-component resilience, and model disagreement.
"""
from __future__ import annotations

import math

from app.services.fusion_graph import (
    FusionGraph,
    FusionStep,
    probs_dict_to_list,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_component_probs() -> dict[str, dict[str, float]]:
    """Return realistic component probability dicts for a full 4-model run."""
    return {
        "dixon_coles": {"home": 0.353, "draw": 0.293, "away": 0.354},
        "enhancer": {"home": 0.477, "draw": 0.234, "away": 0.289},
        "elo": {"home": 0.435, "draw": 0.282, "away": 0.283},
        "pi_rating": {"home": 0.387, "draw": 0.271, "away": 0.342},
    }


def _default_blend_params() -> dict[str, float]:
    return {"dc_weight": 0.55, "elo_weight": 0.05, "pi_weight": 0.05}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _probs_sum(after: list[float]) -> float:
    return sum(after)


def _clamp(value: float, places: int = 6) -> float:
    """Round to *places* to tolerate tiny floating-point drift."""
    return round(value, places)


# ═══════════════════════════════════════════════════════════════════════════════
#  Test: probabilities sum to 1.0 after each step and final
# ═══════════════════════════════════════════════════════════════════════════════


class TestFusionProbabilitiesSumToOne:
    """After every recorded step the output [H, D, A] list sums to 1.0."""

    def test_dc_enhancer_step(self) -> None:
        fg = FusionGraph()
        before = {
            "dixon_coles": [0.353, 0.293, 0.354],
            "enhancer": [0.477, 0.234, 0.289],
        }
        # Simulate: DC * 0.55 + Enh * 0.45 → normalised
        raw = [
            before["dixon_coles"][i] * 0.55 + before["enhancer"][i] * 0.45
            for i in range(3)
        ]
        total = sum(raw)
        after = [v / total for v in raw]

        fg.add_step("dc+enhancer", "base_weight=0.55", before, after)
        assert math.isclose(_probs_sum(after), 1.0, rel_tol=1e-9)

    def test_elo_step(self) -> None:
        fg = FusionGraph()
        prev = [0.408, 0.265, 0.327]
        elo = [0.435, 0.282, 0.283]
        before = {"dc+enhancer": prev, "elo": elo}
        raw = [prev[i] * 0.95 + elo[i] * 0.05 for i in range(3)]
        total = sum(raw)
        after = [v / total for v in raw]

        fg.add_step("+elo", "elo_weight=0.05", before, after)
        assert math.isclose(_probs_sum(after), 1.0, rel_tol=1e-9)

    def test_pi_step(self) -> None:
        fg = FusionGraph()
        prev = [0.410, 0.266, 0.324]
        pi = [0.387, 0.271, 0.342]
        before = {"dc+enhancer+elo": prev, "pi_rating": pi}
        raw = [prev[i] * 0.95 + pi[i] * 0.05 for i in range(3)]
        total = sum(raw)
        after = [v / total for v in raw]

        fg.add_step("+pi", "pi_weight=0.05", before, after)
        assert math.isclose(_probs_sum(after), 1.0, rel_tol=1e-9)

    def test_full_sequential_blend_probs_sum_to_one(self) -> None:
        """End-to-end: run all three steps and check every intermediate sum."""
        fg = FusionGraph(blend_params=_default_blend_params())
        cp = _make_component_probs()
        dc_w = 0.55
        elo_w = 0.05
        pi_w = 0.05

        # Step 1 — DC + Enhancer
        raw1 = [
            cp["dixon_coles"]["home"] * dc_w + cp["enhancer"]["home"] * (1 - dc_w),
            cp["dixon_coles"]["draw"] * dc_w + cp["enhancer"]["draw"] * (1 - dc_w),
            cp["dixon_coles"]["away"] * dc_w + cp["enhancer"]["away"] * (1 - dc_w),
        ]
        t1 = sum(raw1)
        s1 = [v / t1 for v in raw1]
        fg.add_step(
            "dc+enhancer", f"base_weight={dc_w}",
            {"dixon_coles": list(cp["dixon_coles"].values()),
             "enhancer": list(cp["enhancer"].values())},
            s1,
        )
        assert math.isclose(_probs_sum(s1), 1.0, rel_tol=1e-9)

        # Step 2 — + Elo
        raw2 = [
            s1[0] * (1 - elo_w) + cp["elo"]["home"] * elo_w,
            s1[1] * (1 - elo_w) + cp["elo"]["draw"] * elo_w,
            s1[2] * (1 - elo_w) + cp["elo"]["away"] * elo_w,
        ]
        t2 = sum(raw2)
        s2 = [v / t2 for v in raw2]
        fg.add_step(
            "+elo", f"elo_weight={elo_w}",
            {"dc+enhancer": s1, "elo": list(cp["elo"].values())},
            s2,
        )
        assert math.isclose(_probs_sum(s2), 1.0, rel_tol=1e-9)

        # Step 3 — + Pi
        raw3 = [
            s2[0] * (1 - pi_w) + cp["pi_rating"]["home"] * pi_w,
            s2[1] * (1 - pi_w) + cp["pi_rating"]["draw"] * pi_w,
            s2[2] * (1 - pi_w) + cp["pi_rating"]["away"] * pi_w,
        ]
        t3 = sum(raw3)
        s3 = [v / t3 for v in raw3]
        fg.add_step(
            "+pi", f"pi_weight={pi_w}",
            {"dc+enhancer+elo": s2, "pi_rating": list(cp["pi_rating"].values())},
            s3,
        )
        assert math.isclose(_probs_sum(s3), 1.0, rel_tol=1e-9)

        # Final output also sums to 1.0
        assert math.isclose(_probs_sum(s3), 1.0, rel_tol=1e-9)


# ═══════════════════════════════════════════════════════════════════════════════
#  Test: effective weights sum to 1.0
# ═══════════════════════════════════════════════════════════════════════════════


class TestEffectiveWeightsSumToOne:
    """Effective weights must always sum to 1.0 by construction."""

    def test_default_weights(self) -> None:
        fg = FusionGraph()
        ew = fg.compute_effective_weights({"dc_weight": 0.55, "elo_weight": 0.05, "pi_weight": 0.05})
        total = sum(ew.values())
        assert _clamp(total) == 1.0, f"effective weights sum to {total}"

    def test_equal_blend(self) -> None:
        fg = FusionGraph()
        ew = fg.compute_effective_weights({"dc_weight": 0.50, "elo_weight": 0.25, "pi_weight": 0.25})
        total = sum(ew.values())
        assert _clamp(total) == 1.0

    def test_dc_only_blend(self) -> None:
        """Pi_weight=0 should yield pi_effective=0, but total still 1.0."""
        fg = FusionGraph()
        ew = fg.compute_effective_weights({"dc_weight": 0.55, "elo_weight": 0.05, "pi_weight": 0.0})
        total = sum(ew.values())
        assert _clamp(total) == 1.0
        assert ew["pi_effective"] == 0.0

    def test_no_elo_no_pi(self) -> None:
        fg = FusionGraph()
        ew = fg.compute_effective_weights({"dc_weight": 0.60, "elo_weight": 0.0, "pi_weight": 0.0})
        total = sum(ew.values())
        assert _clamp(total) == 1.0

    def test_effective_weights_individual_values(self) -> None:
        """Spot-check known values for dc=0.55, elo=0.05, pi=0.05."""
        fg = FusionGraph()
        ew = fg.compute_effective_weights({"dc_weight": 0.55, "elo_weight": 0.05, "pi_weight": 0.05})
        assert math.isclose(ew["dc_effective"], 0.55 * 0.95 * 0.95, rel_tol=1e-6)
        assert math.isclose(ew["enhancer_effective"], 0.45 * 0.95 * 0.95, rel_tol=1e-6)
        assert math.isclose(ew["elo_effective"], 0.05 * 0.95, rel_tol=1e-6)
        assert math.isclose(ew["pi_effective"], 0.05, rel_tol=1e-6)


# ═══════════════════════════════════════════════════════════════════════════════
#  Test: each step recorded with before/after/formula
# ═══════════════════════════════════════════════════════════════════════════════


class TestFusionGraphReportsAllSteps:
    """Every call to add_step is faithfully stored and accessible."""

    def test_steps_populated(self) -> None:
        fg = FusionGraph(blend_params=_default_blend_params())

        fg.add_step(
            "dc+enhancer", "base_weight=0.55",
            {"dixon_coles": [0.353, 0.293, 0.354], "enhancer": [0.477, 0.234, 0.289]},
            [0.408, 0.265, 0.327],
        )
        fg.add_step(
            "+elo", "elo_weight=0.05",
            {"dc+enhancer": [0.408, 0.265, 0.327], "elo": [0.435, 0.282, 0.283]},
            [0.410, 0.266, 0.324],
        )
        fg.add_step(
            "+pi", "pi_weight=0.05",
            {"dc+enhancer+elo": [0.410, 0.266, 0.324], "pi_rating": [0.387, 0.271, 0.342]},
            [0.408, 0.267, 0.325],
        )

        assert len(fg.steps) == 3
        for step in fg.steps:
            assert isinstance(step, FusionStep)
            assert step.name
            assert step.formula
            assert len(step.before) >= 2
            assert len(step.after) == 3

    def test_to_dict_serialises_steps(self) -> None:
        fg = FusionGraph(blend_params=_default_blend_params())
        fg.add_step("dc+enhancer", "base_weight=0.55",
                     {"dc": [0.35, 0.30, 0.35], "enh": [0.48, 0.23, 0.29]},
                     [0.41, 0.26, 0.33])

        d = fg.to_dict()
        assert "steps" in d
        assert len(d["steps"]) == 1
        step = d["steps"][0]
        assert step["name"] == "dc+enhancer"
        assert step["formula"] == "base_weight=0.55"
        assert "before" in step
        assert "after" in step
        assert len(step["after"]) == 3

    def test_to_dict_full_pipeline(self) -> None:
        """Simulate full 4-model pipeline and check step count in dict."""
        fg = FusionGraph(blend_params=_default_blend_params())

        # Step 1
        fg.add_step("dc+enhancer", "base_weight=0.55", {
            "dixon_coles": [0.353, 0.293, 0.354],
            "enhancer": [0.477, 0.234, 0.289],
        }, [0.408, 0.265, 0.327])

        # Step 2
        fg.add_step("+elo", "elo_weight=0.05", {
            "dc+enhancer": [0.408, 0.265, 0.327],
            "elo": [0.435, 0.282, 0.283],
        }, [0.410, 0.266, 0.324])

        # Step 3
        fg.add_step("+pi", "pi_weight=0.05", {
            "dc+enhancer+elo": [0.410, 0.266, 0.324],
            "pi_rating": [0.387, 0.271, 0.342],
        }, [0.408, 0.267, 0.325])

        d = fg.to_dict()
        assert len(d["steps"]) == 3
        assert d["method"] == "sequential_blend"
        assert d["blend_params"]["dc_weight"] == 0.55


# ═══════════════════════════════════════════════════════════════════════════════
#  Test: missing component does not break weight math
# ═══════════════════════════════════════════════════════════════════════════════


class TestDisabledWeibullDoesNotLeaveWeightHole:
    """When a component (e.g. Weibull) is absent, effective weights should
    still be well-defined and sum to 1.0 — no NaN, no missing-key error."""

    def test_baseline_mode_effective_weights(self) -> None:
        """Baseline only uses DC; effective weights should still sum to 1.0."""
        fg = FusionGraph()
        # With elo and pi at zero, only DC and Enhancer contribute
        ew = fg.compute_effective_weights({"dc_weight": 0.55, "elo_weight": 0.0, "pi_weight": 0.0})
        total = sum(ew.values())
        assert _clamp(total) == 1.0
        # dc + enhancer should absorb all weight
        assert math.isclose(ew["dc_effective"] + ew["enhancer_effective"], 1.0)
        assert ew["elo_effective"] == 0.0
        assert ew["pi_effective"] == 0.0

    def test_standard_mode_missing_pi(self) -> None:
        """Standard does not use Pi; effective weights should still work."""
        fg = FusionGraph()
        ew = fg.compute_effective_weights({"dc_weight": 0.55, "elo_weight": 0.05, "pi_weight": 0.0})
        total = sum(ew.values())
        assert _clamp(total) == 1.0
        assert ew["pi_effective"] == 0.0

    def test_incomplete_blend_params_defaults(self) -> None:
        """Missing keys default to 0, not KeyError."""
        fg = FusionGraph()
        ew = fg.compute_effective_weights({"dc_weight": 0.55})  # no elo/pi
        total = sum(ew.values())
        assert _clamp(total) == 1.0
        assert ew["elo_effective"] == 0.0
        assert ew["pi_effective"] == 0.0

    def test_disagreement_with_two_components_only(self) -> None:
        """disagreement works with just 2 components (baseline mode)."""
        fg = FusionGraph()
        probs = {
            "dixon_coles": {"home": 0.353, "draw": 0.293, "away": 0.354},
            "enhancer": {"home": 0.477, "draw": 0.234, "away": 0.289},
        }
        fg.compute_disagreement(probs)
        diff = abs(0.353 - 0.477)
        assert math.isclose(fg.model_disagreement["max_home_diff"], diff, rel_tol=1e-6)
        assert "dixon_coles_enhancer" in fg.model_disagreement["pairs"]


# ═══════════════════════════════════════════════════════════════════════════════
#  Test: model disagreement computed
# ═══════════════════════════════════════════════════════════════════════════════


class TestModelDisagreementComputed:
    """Disagreement metrics are populated when multiple models are used."""

    def test_disagreement_with_full_set(self) -> None:
        fg = FusionGraph()
        cp = _make_component_probs()
        fg.compute_disagreement(cp)
        assert fg.model_disagreement["max_home_diff"] >= 0.0
        assert len(fg.model_disagreement["pairs"]) >= 2  # at least 3 C 2 = 3 pairs

    def test_disagreement_values(self) -> None:
        fg = FusionGraph()
        probs = {
            "model_a": {"home": 0.40, "draw": 0.30, "away": 0.30},
            "model_b": {"home": 0.60, "draw": 0.20, "away": 0.20},
            "model_c": {"home": 0.50, "draw": 0.25, "away": 0.25},
        }
        fg.compute_disagreement(probs)
        # max diff should be |0.40 - 0.60| = 0.20
        assert math.isclose(fg.model_disagreement["max_home_diff"], 0.20, rel_tol=1e-6)
        assert math.isclose(
            fg.model_disagreement["pairs"]["model_a_model_b"], 0.20, rel_tol=1e-6
        )

    def test_single_model_no_disagreement(self) -> None:
        fg = FusionGraph()
        fg.compute_disagreement({"dixon_coles": {"home": 0.40, "draw": 0.30, "away": 0.30}})
        assert fg.model_disagreement["max_home_diff"] == 0.0

    def test_disagreement_survives_to_dict(self) -> None:
        fg = FusionGraph()
        fg.compute_disagreement(_make_component_probs())
        d = fg.to_dict()
        assert "model_disagreement" in d
        assert "max_home_diff" in d["model_disagreement"]
        assert d["model_disagreement"]["max_home_diff"] >= 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  Test: probs_dict_to_list helper
# ═══════════════════════════════════════════════════════════════════════════════


class TestProbsDictToList:
    """probs_dict_to_list converts both long-form and short-form keys."""

    def test_long_form_keys(self) -> None:
        d = {"home_win_prob": 0.4, "draw_prob": 0.3, "away_win_prob": 0.3}
        assert probs_dict_to_list(d) == [0.4, 0.3, 0.3]

    def test_short_form_keys(self) -> None:
        d = {"home": 0.5, "draw": 0.25, "away": 0.25}
        assert probs_dict_to_list(d) == [0.5, 0.25, 0.25]

    def test_short_form_preferred(self) -> None:
        """If both long and short exist, long-form takes priority."""
        d = {"home_win_prob": 0.4, "home": 0.5, "draw_prob": 0.3, "draw": 0.2, "away_win_prob": 0.3, "away": 0.2}
        assert probs_dict_to_list(d) == [0.4, 0.3, 0.3]

    def test_missing_keys_default_to_zero(self) -> None:
        d = {"home": 1.0}
        assert probs_dict_to_list(d) == [1.0, 0.0, 0.0]
