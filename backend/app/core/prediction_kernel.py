"""Pure prediction kernel for V4.9 accuracy-engine experiments.

The kernel owns deterministic fusion math and provenance shaping.  It does
not read files, call APIs, query databases, write snapshots, or apply model
changes.  Outer services remain responsible for I/O and data assembly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.engine import CoreFusionResult, run_core_fusion


OUTCOME_KEYS = ("home", "draw", "away")


@dataclass(frozen=True)
class ProbabilityDistribution:
    home: float
    draw: float
    away: float

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> "ProbabilityDistribution | None":
        if not isinstance(raw, dict):
            return None
        try:
            home = float(raw.get("home", raw.get("home_win_prob")))
            draw = float(raw.get("draw", raw.get("draw_prob")))
            away = float(raw.get("away", raw.get("away_win_prob")))
        except (TypeError, ValueError):
            return None
        if min(home, draw, away) < 0:
            return None
        total = home + draw + away
        if total <= 0:
            return None
        return cls(home=home / total, draw=draw / total, away=away / total)

    def to_short(self) -> dict[str, float]:
        return {"home": self.home, "draw": self.draw, "away": self.away}

    def to_long(self) -> dict[str, float]:
        return {
            "home_win_prob": self.home,
            "draw_prob": self.draw,
            "away_win_prob": self.away,
        }


@dataclass(frozen=True)
class ComponentPrediction:
    name: str
    probs: ProbabilityDistribution | None
    score_matrix: list[list[float]] | None = None
    source_status: str = "unknown"
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "probs": self.probs.to_short() if self.probs else None,
            "has_score_matrix": self.score_matrix is not None,
            "source_status": self.source_status,
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class MatchContext:
    home_team: str
    away_team: str
    competition: str
    stage: str = ""
    is_neutral: bool = True
    as_of_time: str | None = None
    kickoff_at: str | None = None


@dataclass(frozen=True)
class KernelFeatureSnapshot:
    components: dict[str, ComponentPrediction]
    dc_home_xg: float
    dc_away_xg: float
    weights: dict[str, float]
    source_status: dict[str, Any] = field(default_factory=dict)

    def stable_hash(self) -> str:
        payload = {
            "components": {
                key: value.to_dict()
                for key, value in sorted(self.components.items(), key=lambda item: item[0])
            },
            "dc_home_xg": round(float(self.dc_home_xg), 8),
            "dc_away_xg": round(float(self.dc_away_xg), 8),
            "weights": {key: round(float(value), 8) for key, value in sorted(self.weights.items())},
            "source_status": self.source_status,
        }
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PredictionKernelResult:
    probs: ProbabilityDistribution
    core_fusion: CoreFusionResult
    provenance: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "probs": self.probs.to_long(),
            "core_fusion": asdict(self.core_fusion),
            "provenance": dict(self.provenance),
        }


class PredictionKernel:
    """Deterministic fusion kernel shared by prediction entrypoints."""

    schema_version = "prediction_kernel.v1"

    def run(
        self,
        *,
        context: MatchContext,
        feature_snapshot: KernelFeatureSnapshot,
    ) -> PredictionKernelResult:
        components = feature_snapshot.components
        weights = feature_snapshot.weights
        dc = components.get("dc")
        if dc is None or dc.probs is None:
            raise ValueError("PredictionKernel requires a dc component with probabilities")

        enhancer = components.get("enhancer")
        weibull = components.get("weibull")
        elo = components.get("elo")
        pi = components.get("pi") or components.get("pi_rating")

        core = run_core_fusion(
            dc_probs=dc.probs.to_long(),
            dc_home_xg=feature_snapshot.dc_home_xg,
            dc_away_xg=feature_snapshot.dc_away_xg,
            dc_base_weight=float(weights.get("dc", 1.0)),
            enh_probs=enhancer.probs.to_long() if enhancer and enhancer.probs else None,
            weibull_probs=weibull.probs.to_long() if weibull and weibull.probs else None,
            weibull_weight=float(weights.get("weibull", 0.0)),
            elo_probs=elo.probs.to_long() if elo and elo.probs else None,
            elo_weight=float(weights.get("elo", 0.0)),
            pi_probs=pi.probs.to_long() if pi and pi.probs else None,
            pi_weight=float(weights.get("pi", 0.0)),
        )
        probs = ProbabilityDistribution.from_mapping(core.probs)
        if probs is None:
            raise ValueError("PredictionKernel produced invalid probabilities")
        provenance = {
            "schema_version": self.schema_version,
            "feature_hash": feature_snapshot.stable_hash(),
            "context": asdict(context),
            "component_status": {
                key: value.source_status for key, value in components.items()
            },
            "weights": dict(weights),
            "source_status": dict(feature_snapshot.source_status),
        }
        return PredictionKernelResult(probs=probs, core_fusion=core, provenance=provenance)
