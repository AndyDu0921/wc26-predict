"""LearningEngine — self-evolution via per-match error attribution.

After each match finishes:
1. Compute proper scores for the persisted pre-match distribution
2. Produce approximate, diagnostic component attribution
3. Log model-vs-market divergence with multiclass proper scoring
4. Leave every model/configuration change behind the proposal gate

All writes are idempotent — re-running for the same match replaces old records.

V4.12: ``learning_weight`` controls diagnostic eligibility only.  This module
never mutates production weights, signal multipliers, or model artifacts.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any
from uuid import UUID
from sqlalchemy import select, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prediction_snapshot import PredictionSnapshot
from app.models.prediction_run import PredictionRun
from app.models.prediction_learning_log import PredictionLearningLog
from app.services.evaluation_metrics import (
    score_matrix_log_loss,
    score_matrix_exact_hit,
    score_matrix_top_n_hit,
)
from app.models.market_divergence_log import MarketDivergenceLog

logger = logging.getLogger(__name__)


def _brier(probs: dict[str, float], actual_index: int) -> float:
    """Brier score for a 3-outcome prediction."""
    actual = [0.0, 0.0, 0.0]
    actual[actual_index] = 1.0
    preds = [probs["home"], probs["draw"], probs["away"]]
    return sum((p - a) ** 2 for p, a in zip(preds, actual))


def _result_index(home_goals: int, away_goals: int) -> int:
    """0=home win, 1=draw, 2=away win."""
    if home_goals > away_goals:
        return 0
    if home_goals == away_goals:
        return 1
    return 2


def _is_uuid_like(value: str | None) -> bool:
    """Accept UUIDs stored either dashed or as 32 hex chars."""
    if not value:
        return False
    try:
        UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


def _coerce_probability(value: Any) -> float:
    """Convert one required probability value or reject the payload."""
    if value is None:
        raise ValueError("missing probability")
    try:
        coerced = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid probability") from exc
    if not math.isfinite(coerced) or coerced < 0.0 or coerced > 1.0:
        raise ValueError("probability outside [0, 1]")
    return coerced


def _coerce_probs(probs: dict[str, Any]) -> dict[str, float]:
    """Normalize component probability names without inventing missing values."""
    if not isinstance(probs, dict):
        raise ValueError("probability payload must be a dictionary")
    home = _coerce_probability(probs.get("home", probs.get("home_win_prob")))
    draw = _coerce_probability(probs.get("draw", probs.get("draw_prob")))
    away = _coerce_probability(probs.get("away", probs.get("away_win_prob")))
    total = home + draw + away
    if total <= 0:
        raise ValueError("probability payload has zero total mass")
    return {
        "home": home / total,
        "draw": draw / total,
        "away": away / total,
    }


def _learning_tier(weight: float) -> str:
    """Map learning_weight to action tier."""
    if weight >= 0.70:
        return "full"
    elif weight >= 0.30:
        return "diagnostic"
    else:
        return "record_only"


class LearningEngine:
    """Per-match learning: error attribution, signal tracking, context updates."""

    async def process_match_result(
        self,
        snapshot: PredictionSnapshot,
        home_goals: int,
        away_goals: int,
        db: AsyncSession,
        verified_result_id: str | None = None,
        learning_weight: float = 1.0,
        db_path: str | Path | None = None,
    ) -> PredictionLearningLog:
        """Complete per-match learning cycle.

        Args:
            snapshot: The prediction snapshot to evaluate
            home_goals, away_goals: Actual match result
            db: Active database session
            verified_result_id: UUID string of a consensus row from
                MatchResultVerification.  If None, the learning log is
                written with status="pending_review" and does NOT affect
                production weights.
            learning_weight: 0.0-1.0 from process_evaluator/failure_classifier.
                Controls which sub-steps execute:
                  >= 0.70  "full"       — all steps
                  0.30-0.70 "diagnostic" — attribution + logging only
                  < 0.30   "record_only" — basic error log only
            db_path: Canonical SQLite path for score-calibration telemetry.

        Returns the created PredictionLearningLog record.
        """
        tier = _learning_tier(learning_weight)
        actual_index = _result_index(home_goals, away_goals)

        # 1. Error attribution (always runs — core diagnostic data)
        error_log = await self._attribute_error(
            snapshot, actual_index, db, verified_result_id,
            learning_weight, tier,
            home_goals=home_goals, away_goals=away_goals,
        )

        # ── 1.5 Score calibration drift tracking (V4.7 S2.3) ──
        # Best-effort: a failure here must never block the main learning flow.
        self._track_score_calibration(
            snapshot,
            home_goals,
            away_goals,
            db_path=db_path,
        )

        # 2. Canonical signal attribution is handled by
        # information_state_engine.evaluate_match_signals() in the post-match
        # orchestrator.  Legacy signal_track_record multipliers are never
        # changed here.

        # 3. Market divergence telemetry (full + diagnostic)
        if tier in ("full", "diagnostic"):
            await self._log_market_divergence(snapshot, actual_index, db)

        await db.flush()
        logger.info(
            "Learning complete: %s vs %s, tier=%s, weight=%.2f, Brier=%.3f, dir=%s",
            snapshot.home_team, snapshot.away_team,
            tier, learning_weight,
            error_log.error_magnitude, error_log.error_direction,
        )
        return error_log

    async def _attribute_error(
        self,
        snapshot: PredictionSnapshot,
        actual_index: int,
        db: AsyncSession,
        verified_result_id: str | None = None,
        learning_weight: float = 1.0,
        tier: str = "full",
        home_goals: int = 0,
        away_goals: int = 0,
    ) -> PredictionLearningLog:
        """Attribute prediction error using approximate leave-one-out reconstruction.

        The production chain contains nonlinear guards and calibration, so
        removing a component cannot be replayed exactly from component
        probabilities alone.  These values are diagnostic hypotheses and may
        only become proposals after paired walk-forward validation.

        positive marginal = component helped (removing it made prediction worse)
        negative marginal = component hurt (removing it made prediction better)

        V4.7-score: Also computes score-level metrics from the fused score
        matrix and per-source matrices stored in the snapshot.
        """
        final_probs = _coerce_probs(
            snapshot.adjusted_probs or snapshot.baseline_probs or {}
        )
        attribution_reference = _coerce_probs(
            snapshot.baseline_probs or final_probs
        )
        final_brier = _brier(final_probs, actual_index)
        attribution_reference_brier = _brier(
            attribution_reference,
            actual_index,
        )

        component = snapshot.component_probs or {}
        components = {}
        component_aliases = {
            "dixon_coles": "dc",
            "dc": "dc",
            "enhancer": "enhancer",
            "negbin": "negbin",
            "elo": "elo",
            "pi": "pi",
            "pi_rating": "pi",
            "weibull": "weibull",
            "market": "market",
            "signals": "signals",
        }
        for key, canonical_key in component_aliases.items():
            probs = component.get(key, {})
            if probs:
                try:
                    components[canonical_key] = _coerce_probs(probs)
                except ValueError:
                    logger.warning("Skipping invalid %s component probabilities", key)
        if snapshot.market_probs:
            try:
                components.setdefault("market", _coerce_probs(snapshot.market_probs))
            except ValueError:
                logger.warning("Skipping invalid market probabilities")

        weights, weight_source = self._weights_for_snapshot(
            snapshot,
            components,
        )

        # Leave-one-out marginal contributions
        dc_marginal = None
        enhancer_marginal = None
        elo_marginal = None
        market_marginal = None
        signal_marginal = None

        if components:
            # Without DC: fuse enhancer-only (or enhancer+elo if available)
            without_dc = self._fuse_without(components, weights, exclude="dc")
            if without_dc:
                dc_marginal = (
                    _brier(without_dc, actual_index)
                    - attribution_reference_brier
                )

            # Without Enhancer: fuse dc-only (or dc+elo)
            without_enh = self._fuse_without(components, weights, exclude="enhancer")
            if without_enh:
                enhancer_marginal = (
                    _brier(without_enh, actual_index)
                    - attribution_reference_brier
                )

            # Without Elo: fuse dc+enhancer only
            without_elo = self._fuse_without(components, weights, exclude="elo")
            if without_elo:
                elo_marginal = (
                    _brier(without_elo, actual_index)
                    - attribution_reference_brier
                )

            without_market = self._fuse_without(components, weights, exclude="market")
            if without_market and "market" in components:
                market_marginal = (
                    _brier(without_market, actual_index)
                    - attribution_reference_brier
                )

            without_signal = self._fuse_without(components, weights, exclude="signals")
            if without_signal and "signals" in components:
                signal_marginal = (
                    _brier(without_signal, actual_index)
                    - attribution_reference_brier
                )

        # Old proportional fields — keep for backward compat, set to None
        dc_contrib = None
        enhancer_contrib = None
        elo_contrib = None

        # ── V4.7-score: Score-level evaluation ──
        # Compute log-loss on the fused score matrix and per-source matrices
        # to track which model component contributes most to score prediction.
        score_ll: float | None = None
        score_exact: bool | None = None
        score_top3: bool | None = None
        dc_score_ll: float | None = None
        negbin_score_ll: float | None = None
        weibull_score_ll: float | None = None

        # Fused score matrix (stored in snapshot at prediction time)
        fused_mat = getattr(snapshot, "fused_score_matrix", None)
        if fused_mat is None:
            # Fallback: try pipeline_params for legacy snapshots
            params = snapshot.pipeline_params or {}
            fused_mat = params.get("fused_score_matrix")

        if fused_mat and isinstance(fused_mat, list) and len(fused_mat) > 0:
            try:
                score_ll = score_matrix_log_loss(fused_mat, home_goals, away_goals)
                score_exact = score_matrix_exact_hit(fused_mat, home_goals, away_goals)
                score_top3 = score_matrix_top_n_hit(fused_mat, home_goals, away_goals, n=3)
            except Exception:
                logger.debug("Score matrix log-loss failed for fused matrix", exc_info=True)

        # Per-source score log loss for marginal analysis (Wheatcroft 2021)
        source_mats = getattr(snapshot, "source_score_matrices", None)
        if source_mats is None:
            params = snapshot.pipeline_params or {}
            source_mats = params.get("source_score_matrices")

        if source_mats and isinstance(source_mats, dict):
            try:
                dc_mat = source_mats.get("dc")
                if dc_mat and isinstance(dc_mat, list) and len(dc_mat) > 0:
                    dc_score_ll = score_matrix_log_loss(dc_mat, home_goals, away_goals)

                nb_mat = source_mats.get("negbin")
                if nb_mat and isinstance(nb_mat, list) and len(nb_mat) > 0:
                    negbin_score_ll = score_matrix_log_loss(nb_mat, home_goals, away_goals)

                wb_mat = source_mats.get("weibull")
                if wb_mat and isinstance(wb_mat, list) and len(wb_mat) > 0:
                    weibull_score_ll = score_matrix_log_loss(wb_mat, home_goals, away_goals)
            except Exception:
                logger.debug("Per-source score log-loss failed", exc_info=True)

        # Error direction
        pred_home = final_probs["home"]
        pred_draw = final_probs["draw"]
        pred_away = final_probs["away"]
        pred_index = max(range(3), key=lambda i: [pred_home, pred_draw, pred_away][i])
        if pred_index == actual_index:
            direction = "correct"
        elif pred_index == 0 and actual_index != 0:
            direction = "overestimate_home"
        elif pred_index == 2 and actual_index != 2:
            direction = "overestimate_away"
        else:
            direction = "mispredict"

        # Resolve learning status from verification state
        learning_status = await self._resolve_learning_status(db, verified_result_id)
        prediction_run_id = await self._resolve_prediction_run_id(snapshot, db)

        # Delete any previous log for this snapshot (idempotent)
        if snapshot.id:
            await db.execute(
                delete(PredictionLearningLog).where(
                    PredictionLearningLog.snapshot_id == snapshot.id
                )
            )

        log = PredictionLearningLog(
            match_id=snapshot.match_id or None,
            prediction_run_id=prediction_run_id,
            snapshot_id=snapshot.id or None,
            status=learning_status,
            error_magnitude=final_brier,
            error_direction=direction,
            model_was_right=pred_index == actual_index,
            dc_error_contribution=dc_contrib,
            enhancer_error_contribution=enhancer_contrib,
            elo_error_contribution=elo_contrib,
            dc_marginal=dc_marginal,
            enhancer_marginal=enhancer_marginal,
            elo_marginal=elo_marginal,
            market_marginal=market_marginal,
            signal_marginal=signal_marginal,
            # V4.7-score: score-level evaluation metrics
            score_log_loss=score_ll,
            score_exact_hit=score_exact,
            score_top3_hit=score_top3,
            dc_score_log_loss=dc_score_ll,
            negbin_score_log_loss=negbin_score_ll,
            weibull_score_log_loss=weibull_score_ll,
            learning_weight=learning_weight,
            learning_tier=tier,
            context_tags={
                "attribution_method": "approximate_sequential_reconstruction_v3",
                "attribution_is_exact": False,
                "production_mutation": False,
                "weight_source": weight_source,
                "reference": (
                    "snapshot_baseline"
                    if snapshot.baseline_probs
                    else "final_probability_fallback"
                ),
                "learning_tier": tier,
                "learning_weight": learning_weight,
            },
        )
        db.add(log)
        return log

    @staticmethod
    def _track_score_calibration(
        snapshot: PredictionSnapshot,
        home_goals: int,
        away_goals: int,
        *,
        db_path: str | Path | None = None,
    ) -> None:
        """Update score calibration drift tracking (V4.7 S2.3).

        Best-effort only — failures are logged but never propagate.
        Compares the fused score matrix's per-bucket probability mass against
        the actual total-goals outcome to detect systematic miscalibration.
        """
        try:
            fused_mat = getattr(snapshot, "fused_score_matrix", None)
            if fused_mat is None:
                params = snapshot.pipeline_params or {}
                fused_mat = params.get("fused_score_matrix")

            if fused_mat is None or not isinstance(fused_mat, list) or len(fused_mat) == 0:
                logger.debug("No fused score matrix for match %s — skipping calibration drift",
                             snapshot.match_id)
                return

            from app.services.score_calibration_tracker import log_score_calibration

            log_score_calibration(
                match_id=str(snapshot.match_id),
                home_goals=home_goals,
                away_goals=away_goals,
                score_matrix=fused_mat,
                snapshot_id=str(snapshot.id) if snapshot.id else None,
                db_path=db_path,
            )
        except Exception:
            logger.debug("Score calibration tracking skipped for %s vs %s",
                         snapshot.home_team, snapshot.away_team, exc_info=True)

    async def _resolve_learning_status(
        self,
        db: AsyncSession,
        verified_result_id: str | None,
    ) -> str:
        """Determine the learning log status based on verification state.

        Returns:
            "active" if a verified consensus exists,
            "pending_review" otherwise.
        """
        if verified_result_id is None:
            return "pending_review"

        from uuid import UUID
        from app.models.match_result_verification import MatchResultVerification

        try:
            vid = UUID(verified_result_id)
        except (ValueError, TypeError):
            logger.warning(
                "verified_result_id=%s is not a valid UUID, falling back to pending_review",
                verified_result_id,
            )
            return "pending_review"

        result = await db.execute(
            select(MatchResultVerification).where(
                MatchResultVerification.id == vid
            )
        )
        verification = result.scalar_one_or_none()
        if verification is None:
            logger.warning(
                "verified_result_id=%s not found in DB, falling back to pending_review",
                verified_result_id,
            )
            return "pending_review"

        if verification.is_consensus:
            return "active"

        logger.warning(
            "verified_result_id=%s exists but is_consensus=False, falling back to pending_review",
            verified_result_id,
        )
        return "pending_review"

    async def _resolve_prediction_run_id(
        self,
        snapshot: PredictionSnapshot,
        db: AsyncSession,
    ) -> str | None:
        """Best-effort link from a script snapshot to the canonical prediction run."""
        raw_match_id = str(snapshot.match_id or "").strip()
        candidates = [raw_match_id]
        clean = raw_match_id.replace("-", "")
        if clean and clean not in candidates:
            candidates.append(clean)
        if len(clean) == 32:
            hyphenated = f"{clean[:8]}-{clean[8:12]}-{clean[12:16]}-{clean[16:20]}-{clean[20:]}"
            if hyphenated not in candidates:
                candidates.append(hyphenated)

        for candidate in candidates:
            result = await db.execute(
                text(
                    "SELECT id FROM prediction_runs "
                    "WHERE CAST(match_id AS TEXT) = :match_id "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"match_id": candidate},
            )
            row = result.mappings().first()
            if row is not None:
                return str(row["id"])

        if not _is_uuid_like(raw_match_id):
            return None
        match_uuid = UUID(raw_match_id)
        result = await db.execute(
            select(PredictionRun)
            .where(PredictionRun.match_id == match_uuid)
            .order_by(PredictionRun.created_at.desc())
            .limit(1)
        )
        run = result.scalar_one_or_none()
        return str(run.id) if run is not None else None

    @staticmethod
    def _weights_for_snapshot(
        snapshot: PredictionSnapshot,
        components: dict[str, dict[str, float]],
    ) -> tuple[dict[str, float], str]:
        """Return historical blend weights, with an explicit legacy fallback."""
        params = snapshot.pipeline_params or {}
        historical = params.get("weight_config") if isinstance(params, dict) else None
        if isinstance(historical, dict) and historical.get("dc") is not None:
            dc_weight = float(historical["dc"])
            weights = {
                "dc": dc_weight,
                "enhancer": 1.0 - dc_weight,
                "negbin": float(params.get("negbin_weight", 0.05)),
                "elo": float(historical.get("elo", 0.0)),
                "pi": float(historical.get("pi", 0.0)),
                "weibull": float(historical.get("weibull", 0.0)),
                "market": float(params.get("market_weight_used", 0.0)),
                "signals": 0.0,
            }
            if "negbin" not in components:
                weights["negbin"] = 0.0
            return weights, "snapshot"

        from app.services.weights import get_weight_config

        stage = str(params.get("stage") or "")
        wc = get_weight_config(
            snapshot.competition or "FIFA World Cup 2026",
            stage,
        )
        return {
            "dc": wc.dc,
            "enhancer": 1.0 - wc.dc,
            "negbin": 0.05 if "negbin" in components else 0.0,
            "elo": wc.elo,
            "pi": wc.pi,
            "weibull": wc.weibull,
            "market": wc.market_max if "market" in components else 0.0,
            "signals": 0.0,
        }, "current_config_fallback"

    @staticmethod
    def _fuse_without(
        components: dict[str, dict[str, float]],
        weights: dict[str, float],
        *,
        exclude: str,
    ) -> dict[str, float] | None:
        """Approximately fuse remaining components after excluding one layer.

        V4.3.0 S6: Uses true sequential fusion matching the production pipeline
        (DC → Enhancer → Weibull → Elo → Pi → Market), not a flat weighted
        average. Nonlinear post-fusion guards and calibrators are not
        reconstructable here, so callers must label the result approximate.
        """
        # Fusion order (same as production pipeline)
        FUSION_ORDER = [
            "dc",
            "enhancer",
            "negbin",
            "weibull",
            "elo",
            "pi",
            "market",
            "signals",
        ]
        remaining_order = [k for k in FUSION_ORDER if k in components and k != exclude]

        if not remaining_order:
            return None

        # Start with the first remaining component
        first = remaining_order[0]
        fused = {
            "home": float(components[first].get("home", 0.33)),
            "draw": float(components[first].get("draw", 0.33)),
            "away": float(components[first].get("away", 0.33)),
        }

        # Sequentially fuse remaining components
        for comp_name in remaining_order[1:]:
            comp = components[comp_name]
            w = weights.get(comp_name, 0.0)
            if w <= 0:
                continue
            fused["home"] = fused["home"] * (1.0 - w) + float(comp.get("home", fused["home"])) * w
            fused["draw"] = fused["draw"] * (1.0 - w) + float(comp.get("draw", fused["draw"])) * w
            fused["away"] = fused["away"] * (1.0 - w) + float(comp.get("away", fused["away"])) * w

        # Normalize
        total = fused["home"] + fused["draw"] + fused["away"]
        if total <= 0:
            return None
        return {
            "home": fused["home"] / total,
            "draw": fused["draw"] / total,
            "away": fused["away"] / total,
        }

    async def _log_market_divergence(
        self,
        snapshot: PredictionSnapshot,
        actual_index: int,
        db: AsyncSession,
    ) -> None:
        """Record whether model or market was closer when they disagreed."""
        market = snapshot.market_probs
        if not market or not isinstance(market, dict):
            return

        params = snapshot.pipeline_params or {}
        pre_market = (
            params.get("pre_market_probs")
            if isinstance(params, dict)
            else None
        )
        try:
            model_probs = _coerce_probs(pre_market or snapshot.baseline_probs or {})
            market_probs = _coerce_probs(market)
        except ValueError:
            logger.warning("Skipping market divergence with incomplete probabilities")
            return
        model_home = model_probs["home"]
        market_home = market_probs["home"]
        divergence = max(
            abs(model_probs[label] - market_probs[label])
            for label in ("home", "draw", "away")
        )

        # Only log significant divergences
        if divergence < 0.12:
            return

        # Determine who was closer
        actual_labels = ["H", "D", "A"]
        actual_label = actual_labels[actual_index]
        model_error = _brier(model_probs, actual_index)
        market_error = _brier(market_probs, actual_index)

        log = MarketDivergenceLog(
            match_id=snapshot.match_id or None,
            divergence_magnitude=divergence,
            model_home_prob=model_home,
            market_home_prob=market_home,
            actual_result=actual_label,
            model_was_closer=model_error < market_error,
        )
        db.add(log)

# Singleton
_engine: LearningEngine | None = None


def get_learning_engine() -> LearningEngine:
    global _engine
    if _engine is None:
        _engine = LearningEngine()
    return _engine
