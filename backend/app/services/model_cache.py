"""Stable pickle payload types for fitted DC and enhancer artifacts.

The classes remain in this module because existing registered pickle artifacts
encode their fully qualified import path. Runtime model caching and implicit
refitting were removed; the active artifact bundle is now the sole model source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class CachedDC:
    """Serializable snapshot of a fitted Dixon-Coles model."""
    attack_params: dict[str, float]
    defense_params: dict[str, float]
    home_advantage: float
    rho: float
    _team_order: list[str]
    trained_at: datetime


@dataclass
class CachedEnhancer:
    """Serializable snapshot of a fitted TabularEnhancer model."""
    model: Any           # fitted HGBClassifier or XGBClassifier
    feature_columns: list[str]
    training_sample_count: int
    fitted_at: datetime
