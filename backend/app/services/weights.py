"""WeightConfig — single source of truth for prediction model weights.

Replaces scattered hardcoded weights in legacy prediction paths and
learning_engine.py.

Production weights are code-versioned competition defaults. Database optimizer
rows are retained as historical evidence but are never loaded automatically.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WeightConfig:
    """Immutable weight configuration for a prediction run.

    All weights are in [0, 1]. They are applied sequentially in the pipeline:
      DC → +Enhancer (1-dc) → +Weibull → +Elo → +Pi → +Market → +Signal

    IMPORTANT: The ``enhancer`` field is INFORMATIONAL only — used by
    learning_engine.py for margin-attribution, NOT by predict_match_full.py
    for controlling the enhancer blend.  The actual enhancer weight in the
    DC+Enhancer fusion step is ``1 - dc``.  To reduce enhancer influence,
    INCREASE ``dc`` (not decrease ``enhancer``).
    """

    version: str = "1.0"
    dc: float = 0.55  # Dixon-Coles base weight in DC+Enhancer fusion (enhancer blend = 1-dc)
    enhancer: float = 0.25  # INFORMATIONAL: used by learning_engine margin attribution only
    elo: float = 0.05  # Elo kappa-Davidson weight
    pi: float = 0.05  # Pi-Rating weight
    weibull: float = 0.10  # Weibull Copula weight
    market_max: float = 0.10  # Market consensus maximum blend
    active: bool = True  # Whether this config is active/approved

    label: str = "DEFAULT"  # Human-readable label for logging

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "dc": self.dc,
            "enhancer": self.enhancer,
            "elo": self.elo,
            "pi": self.pi,
            "weibull": self.weibull,
            "market_max": self.market_max,
            "active": self.active,
            "label": self.label,
        }

    @property
    def dc_enhancer_blend(self) -> float:
        """Base weight for Dixon-Coles in DC+Enhancer fusion.

        fuse_outcome_probabilities(base_weight=self.dc) means:
          fused = DC * dc + Enhancer * (1-dc)
        """
        return self.dc

    @property
    def enhancer_complement(self) -> float:
        """Enhancer weight = 1 - dc."""
        return 1.0 - self.dc


# ── Competition-aware defaults ──
# These are the code-level defaults used when the DB has no entry.
# They match the snapshot.py _get_model_config() logic.

_WORLD_CUP = WeightConfig(
    version="4.7.0-alpha",
    dc=0.90,
    enhancer=0.10,
    elo=0.12,
    pi=0.17,
    weibull=0.10,
    market_max=0.30,
    label="WORLD_CUP_V4.7.0_ALPHA",
)

# Historical production configuration retained unchanged in V4.12. These
# numbers are configuration facts, not evidence that this cohort is optimal.
_WORLD_CUP_KNOCKOUT = WeightConfig(
    version="4.8.1-knockout",
    dc=0.90,
    enhancer=0.10,
    elo=0.24,
    pi=0.22,
    weibull=0.05,
    market_max=0.35,
    label="WORLD_CUP_KNOCKOUT_V4.8.1_ALPHA",
)

_UCL_FINAL = WeightConfig(
    version="1.0",
    dc=0.42,
    enhancer=0.58,      # = 1-dc
    elo=0.08,
    pi=0.12,
    weibull=0.08,
    market_max=0.08,
    label="UCL_FINAL",
)

_UCL_KNOCKOUT = WeightConfig(
    version="1.0",
    dc=0.45,
    enhancer=0.55,      # = 1-dc
    elo=0.07,
    pi=0.10,
    weibull=0.10,
    market_max=0.10,
    label="UCL_KNOCKOUT",
)

_LEAGUE_DEFAULT = WeightConfig(
    version="1.0",
    dc=0.50,
    enhancer=0.50,      # = 1-dc
    elo=0.05,
    pi=0.05,
    weibull=0.10,
    market_max=0.10,
    label="LEAGUE",
)

# Legacy friendly configuration. It remains code-versioned and is not
# automatically updated from the learning-log or optimizer tables.
_FRIENDLY = WeightConfig(
    version="2.7",
    dc=0.28,
    enhancer=0.72,
    elo=0.02,
    pi=0.16,
    weibull=0.12,
    market_max=0.10,
    label="FRIENDLY_ADJUSTED_V2",
)


def get_weight_config(
    competition: str = "",
    stage: str = "",
) -> WeightConfig:
    """Get the weight configuration for a given competition and stage.

    Priority: competition-aware code defaults, then the generic league default.

    Args:
        competition: Competition name (e.g., "FIFA World Cup 2026")
        stage: Match stage (e.g., "Group A - Matchday 1", "Final")

    Returns:
        WeightConfig with the appropriate weights.
    """
    c = competition.lower()
    s = (stage or "").lower()

    # Friendly and World Cup configurations are explicit code defaults.
    if any(kw in c for kw in ["friendly", "international friendly", "warm-up"]):
        return _FRIENDLY

    if "world cup" in c:
        if _is_knockout_stage(s):
            return _WORLD_CUP_KNOCKOUT
        return _WORLD_CUP

    if ("champions" in c or "ucl" in c):
        if s == "final":
            return _UCL_FINAL
        if any(k in s for k in ["quarter", "semi", "last_16", "playoff"]):
            return _UCL_KNOCKOUT
        return _UCL_KNOCKOUT  # UCL group/league phase also uses knockout weights

    return _LEAGUE_DEFAULT


# ── Convenience ──

def _is_knockout_stage(stage: str) -> bool:
    """Detect whether a stage string indicates a knockout (not group) match."""
    s = (stage or "").lower()
    knockout_keywords = [
        "round of 32", "round of 16", "round of 8",
        "quarter", "semi", "final",
        "last 16", "last 32", "last 8",
        "playoff", "knockout",
    ]
    return any(kw in s for kw in knockout_keywords)


def get_world_cup_weights() -> WeightConfig:
    """Get the standard World Cup weight configuration."""
    return get_weight_config(competition="FIFA World Cup 2026", stage="Group A - Matchday 1")
