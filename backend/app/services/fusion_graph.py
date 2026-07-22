"""FusionGraph — sequential multi-model blending graph.

Records each fusion step and computes effective weights and model disagreement
for the WC26 Predict ensemble pipeline.

Typical usage::

    fg = FusionGraph(blend_params={"dc_weight": 0.55, "elo_weight": 0.05, "pi_weight": 0.05})
    fg.add_step("dc+enhancer", "base_weight=0.55", before_dict, after_list)
    fg.compute_effective_weights()
    fg.compute_disagreement(component_probs)
    logger.info(fg.to_dict())
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FusionStep:
    """A single sequential-blend step in the multi-model fusion pipeline.

    Attributes:
        name: Human-readable label (e.g. ``"dc+enhancer"``, ``"+elo"``).
        formula: Short description of the blend formula used.
        before: Map of component names to their ``[home, draw, away]``
            probability lists *before* this step.
        after: The resulting ``[home, draw, away]`` probability list
            *after* blending.
    """

    name: str
    formula: str
    before: dict[str, list[float]]
    after: list[float]


# ── Helper ──────────────────────────────────────────────────────────────────


def probs_dict_to_list(
    probs: dict[str, float],
) -> list[float]:
    """Convert a probability dict to a ``[home, draw, away]`` list.

    Accepts both the ``home_win_prob`` / ``draw_prob`` / ``away_win_prob``
    (long-form) and ``home`` / ``draw`` / ``away`` (short-form) key conventions.
    """
    return [
        float(probs.get("home_win_prob", probs.get("home", 0.0))),
        float(probs.get("draw_prob", probs.get("draw", 0.0))),
        float(probs.get("away_win_prob", probs.get("away", 0.0))),
    ]


# ── FusionGraph ─────────────────────────────────────────────────────────────


@dataclass
class FusionGraph:
    """Sequential multi-model blending graph.

    Tracks each fusion step (component inputs → blended output) and
    computes effective per-model weights and cross-model disagreement.

    **Blend sequence (standard pipeline):**

    #. ``DC * dc_weight + Enhancer * (1 - dc_weight)`` → normalise
    #. ``previous * (1 - weibull_weight) + Weibull * weibull_weight`` → normalise
    #. ``previous * (1 - elo_weight) + Elo * elo_weight`` → normalise
    #. ``previous * (1 - pi_weight) + Pi * pi_weight`` → normalise

    **Effective weight derivation** (expands the sequential chain)::

        dc_effective       =  dc_weight * (1 - weibull_weight) * (1 - elo_weight) * (1 - pi_weight)
        enhancer_effective = (1 - dc_weight) * (1 - weibull_weight) * (1 - elo_weight) * (1 - pi_weight)
        weibull_effective  =  weibull_weight * (1 - elo_weight) * (1 - pi_weight)
        elo_effective      =  elo_weight * (1 - pi_weight)
        pi_effective       =  pi_weight

    Note that the five effective weights always sum to 1.0 by construction.
    """

    method: str = "sequential_blend"
    blend_params: dict[str, float] = field(default_factory=dict)
    steps: list[FusionStep] = field(default_factory=list)
    effective_weights: dict[str, float] = field(default_factory=dict)
    model_disagreement: dict[str, float] = field(default_factory=dict)

    # ── Step recording ───────────────────────────────────────────────────

    def add_step(
        self,
        name: str,
        formula: str,
        before: dict[str, list[float]],
        after: list[float],
    ) -> None:
        """Record a fusion step.

        Args:
            name: Human-readable step name (e.g. ``"dc+enhancer"``, ``"+elo"``).
            formula: Short description of the blend formula.
            before: Dict mapping component/layer names to their ``[home, draw, away]``
                probability triples **before** blending.
            after: The resulting ``[home, draw, away]`` probability triple.
        """
        self.steps.append(FusionStep(name=name, formula=formula, before=before, after=after))

    # ── Analytics ─────────────────────────────────────────────────────────

    def compute_effective_weights(
        self,
        blend_params: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """Compute effective per-model weights from the sequential blend params.

        Args:
            blend_params: Override for ``self.blend_params``.  When ``None``
                the already-stored params are used.

        Returns:
            Dict of ``{dc_effective, enhancer_effective, weibull_effective,
            elo_effective, pi_effective}``.  The five values always sum to 1.0.
        """
        if blend_params is not None:
            self.blend_params = blend_params
        dc_w = float(self.blend_params.get("dc_weight", 0.0))
        wb_w = float(self.blend_params.get("weibull_weight", 0.0))
        elo_w = float(self.blend_params.get("elo_weight", 0.0))
        pi_w = float(self.blend_params.get("pi_weight", 0.0))

        self.effective_weights = {
            "dc_effective": round(dc_w * (1.0 - wb_w) * (1.0 - elo_w) * (1.0 - pi_w), 6),
            "enhancer_effective": round((1.0 - dc_w) * (1.0 - wb_w) * (1.0 - elo_w) * (1.0 - pi_w), 6),
            "weibull_effective": round(wb_w * (1.0 - elo_w) * (1.0 - pi_w), 6),
            "elo_effective": round(elo_w * (1.0 - pi_w), 6),
            "pi_effective": round(pi_w, 6),
        }
        return dict(self.effective_weights)

    def compute_disagreement(
        self,
        component_probs: dict[str, dict[str, float] | list[float]],
    ) -> dict[str, float]:
        """Compute the maximum home-win probability disagreement between models.

        For every pair of components the absolute difference in their
        home-win probability is calculated; the largest such difference is
        reported as ``max_home_diff``.

        Args:
            component_probs: Map of component names to either
                ``{"home": h, "draw": d, "away": a}`` dicts or
                ``[home, draw, away]`` lists.

        Returns:
            Dict with ``"max_home_diff"`` (largest pair-wise home-prob
            difference) and ``"pairs"`` (individual pair diffs).
        """
        home_probs: dict[str, float] = {}
        for name, probs in component_probs.items():
            if isinstance(probs, dict):
                hp = probs.get("home_win_prob", probs.get("home", 0.0))
            elif isinstance(probs, (list, tuple)):
                hp = probs[0] if len(probs) > 0 else 0.0
            else:
                hp = 0.0
            home_probs[name] = float(hp)

        if len(home_probs) < 2:
            self.model_disagreement = {"max_home_diff": 0.0, "pairs": {}}
            return dict(self.model_disagreement)

        max_diff = 0.0
        pairs: dict[str, float] = {}
        names = list(home_probs.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                diff = abs(home_probs[names[i]] - home_probs[names[j]])
                pairs[f"{names[i]}_{names[j]}"] = round(diff, 6)
                max_diff = max(max_diff, diff)

        self.model_disagreement = {
            "max_home_diff": round(max_diff, 6),
            "pairs": pairs,
        }
        return dict(self.model_disagreement)

    # ── Serialisation ────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize the fusion graph to a plain dict for JSON output."""
        return {
            "method": self.method,
            "blend_params": dict(self.blend_params),
            "effective_weights": dict(self.effective_weights),
            "model_disagreement": dict(self.model_disagreement),
            "steps": [
                {
                    "name": s.name,
                    "formula": s.formula,
                    "before": {k: list(v) for k, v in s.before.items()},
                    "after": list(s.after),
                }
                for s in self.steps
            ],
        }
