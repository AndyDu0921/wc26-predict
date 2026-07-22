from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class RunQuality:
    pipeline_status: str = "full"  # full | degraded | failed
    model_components: dict = field(default_factory=lambda: {
        "dixon_coles": "skipped",
        "tabular_enhancer": "skipped",
        "weibull": "skipped",
        "elo": "skipped",
        "pi_rating": "skipped",
        "signal_adjuster": "skipped",
        "market_shadow": "skipped",
    })
    cache: dict = field(default_factory=lambda: {
        "dixon_coles": "miss",
        "tabular": "miss",
    })
    fact_check: str = "skipped"  # passed | failed | skipped
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pipeline_status": self.pipeline_status,
            "model_components": self.model_components,
            "cache": self.cache,
            "fact_check": self.fact_check,
            "warnings": self.warnings,
        }

    def mark_degraded(self, reason: str) -> None:
        self.pipeline_status = "degraded"
        self.warnings.append(reason)
