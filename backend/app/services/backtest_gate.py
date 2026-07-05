"""Backtest gate for model-weight proposals.

The gate is deliberately conservative: it can record a proposal and mark it
as passed, but it never applies weights to production configuration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model_weight_proposal import ModelWeightProposal


LOWER_IS_BETTER_METRICS = ("brier", "logloss", "rps")
OPTIONAL_LOWER_IS_BETTER_METRICS = ("ece",)
WEIGHT_KEYS = ("dc", "elo", "pi", "weibull", "market_max")


@dataclass(frozen=True)
class WeightProposalCandidate:
    """A candidate weight change plus paired backtest evidence."""

    competition: str
    stage: str | None
    base_weights: dict[str, float]
    candidate_weights: dict[str, float]
    metrics: dict[str, float]
    sample_count: int
    fold_count: int | None = None
    source: str = "manual"
    evidence: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None


@dataclass(frozen=True)
class BacktestGateDecision:
    """Result of evaluating a weight proposal."""

    passed: bool
    status: str
    reasons: list[str]
    metric_deltas: dict[str, float]
    weight_deltas: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "status": self.status,
            "reasons": list(self.reasons),
            "metric_deltas": dict(self.metric_deltas),
            "weight_deltas": dict(self.weight_deltas),
        }


class BacktestGate:
    """Validate model-weight candidates before human approval."""

    def __init__(
        self,
        *,
        min_sample_count: int = 30,
        min_fold_count: int = 1,
        max_weight_delta: float = 0.03,
        min_improvement: float = 0.001,
    ) -> None:
        self.min_sample_count = int(min_sample_count)
        self.min_fold_count = int(min_fold_count)
        self.max_weight_delta = float(max_weight_delta)
        self.min_improvement = float(min_improvement)

    def evaluate(self, proposal: WeightProposalCandidate) -> BacktestGateDecision:
        reasons: list[str] = []
        metric_deltas: dict[str, float] = {}
        weight_deltas: dict[str, float] = {}

        if proposal.sample_count < self.min_sample_count:
            reasons.append(
                f"sample_count {proposal.sample_count} < {self.min_sample_count}"
            )
        if (proposal.fold_count or 0) < self.min_fold_count:
            reasons.append(f"fold_count {proposal.fold_count or 0} < {self.min_fold_count}")

        for key in WEIGHT_KEYS:
            base = proposal.base_weights.get(key)
            candidate = proposal.candidate_weights.get(key)
            if base is None or candidate is None:
                reasons.append(f"missing weight key: {key}")
                continue
            base_f = float(base)
            candidate_f = float(candidate)
            if not 0.0 <= candidate_f <= 1.0:
                reasons.append(f"candidate {key}={candidate_f:.4f} outside [0,1]")
            delta = candidate_f - base_f
            weight_deltas[key] = delta
            if abs(delta) > self.max_weight_delta:
                reasons.append(
                    f"{key} delta {delta:+.4f} exceeds max {self.max_weight_delta:.4f}"
                )

        for metric in LOWER_IS_BETTER_METRICS:
            current_key = f"current_{metric}"
            candidate_key = f"candidate_{metric}"
            if current_key not in proposal.metrics or candidate_key not in proposal.metrics:
                reasons.append(f"missing paired metric: {metric}")
                continue
            delta = float(proposal.metrics[candidate_key]) - float(
                proposal.metrics[current_key]
            )
            metric_deltas[metric] = delta
            if delta > 0:
                reasons.append(f"{metric} worsened by {delta:.6f}")

        for metric in OPTIONAL_LOWER_IS_BETTER_METRICS:
            current_key = f"current_{metric}"
            candidate_key = f"candidate_{metric}"
            if current_key in proposal.metrics and candidate_key in proposal.metrics:
                delta = float(proposal.metrics[candidate_key]) - float(
                    proposal.metrics[current_key]
                )
                metric_deltas[metric] = delta
                if delta > self.min_improvement:
                    reasons.append(f"{metric} worsened by {delta:.6f}")

        core_metric_deltas = {
            key: metric_deltas[key]
            for key in LOWER_IS_BETTER_METRICS
            if key in metric_deltas
        }
        best_improvement = min(core_metric_deltas.values(), default=0.0)
        if best_improvement > -self.min_improvement:
            reasons.append(
                "no core scoring metric improved by at least "
                f"{self.min_improvement:.6f}"
            )

        passed = not reasons
        return BacktestGateDecision(
            passed=passed,
            status="gate_passed" if passed else "gate_rejected",
            reasons=reasons,
            metric_deltas=metric_deltas,
            weight_deltas=weight_deltas,
        )

    async def persist(
        self,
        db: AsyncSession,
        proposal: WeightProposalCandidate,
        decision: BacktestGateDecision | None = None,
    ) -> ModelWeightProposal:
        """Persist a proposal and its gate decision."""
        decision = decision or self.evaluate(proposal)
        record = ModelWeightProposal(
            competition=proposal.competition,
            stage=proposal.stage,
            source=proposal.source,
            status=decision.status,
            sample_count=proposal.sample_count,
            fold_count=proposal.fold_count,
            max_weight_delta=self.max_weight_delta,
            min_improvement=self.min_improvement,
            base_weights=dict(proposal.base_weights),
            candidate_weights=dict(proposal.candidate_weights),
            metrics=dict(proposal.metrics),
            gate_decision=decision.to_dict(),
            evidence=dict(proposal.evidence or {}),
            notes=proposal.notes,
        )
        db.add(record)
        await db.flush()
        return record

    @staticmethod
    def knockout_mode() -> "BacktestGate":
        """Factory for knockout-stage BacktestGate with relaxed parameters.

        KO stage has fewer samples (8-16 matches) than group stage (48+).
        Relaxing the gate allows the self-evolution system to make small,
        evidence-backed adjustments during knockout tournaments.

        IMPORTANT: These parameters should be restored to defaults after
        the knockout stage ends.

        Returns:
            BacktestGate configured for knockout tournament evaluation.
        """
        return BacktestGate(
            min_sample_count=8,     # 30→8: KO rounds have 8-16 samples
            max_weight_delta=0.05,  # 0.03→0.05: allow slightly larger adjustments
            min_improvement=0.002,  # 0.001→0.002: require stronger evidence
        )
