"""PredictionPipeline — single, unified prediction entry point for WC26.

Replaces scattered prediction logic across legacy snapshot/fast prediction
paths and is now the model core behind CLI, API, and worker triggers.

Design: Wraps the proven pipeline from snapshot.py into a reusable class.
Does NOT rewrite business logic — just orchestrates existing services.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.engine import apply_market_boost, enforce_draw_floor, fuse_dc_enhancer_adaptive
from app.services.calibration import IsotonicCalibrator
from app.services.artifact_bundle import active_bundle_provenance, verified_artifact_path
from app.services.dixon_coles import DixonColesModel
from app.services.elo_ratings import EloRatingSystem
from app.services.market_calibrator import MarketCalibrator, get_calibrator
from app.services.pi_ratings import PiRatingWrapper
from app.services.sqlite_paths import current_sync_sqlite_path
from app.services.prediction_kernel_adapter import run_prediction_kernel_from_components
from app.services.prediction_result import DegradedReason, PredictionResult, SourceStatus
from app.services.score_matrix_calibrator import (
    SCORE_MATRIX_CALIBRATION_ENABLED,
    calibrate_score_matrix,
)
from app.core.score_matrix_fusion import build_score_matrix_fusion
from app.core.ko_draw_guard import check_ko_draw_guard, enforce_ko_draw_post_calibration, _is_ko_stage
from app.core.weibull_scenario import classify_weibull_scenario, resolve_weibull_action
from app.core.engine import apply_market_consensus_gate
from app.core.verification_gates import postflight_check
from app.services.tabular_match_model import TabularMatchEnhancer
from app.services.weibull_model import WeibullWrapper
from app.services.weights import get_weight_config

logger = logging.getLogger(__name__)

# ── Constants ──
DEFAULT_COMPETITION_WEIGHT = 0.9
WORLD_CUP_COMPETITION_WEIGHT = 1.5
FRIENDLY_COMPETITION_WEIGHT = 0.5

def _load_isotonic_calibrator(competition: str = "") -> IsotonicCalibrator:
    """Load isotonic calibrator with WC-specific fallback.

    Priority: calibrator_wc.json (if WC, ≥20 samples) → calibrator.json.

    Both files are part of the active artifact bundle. Hash mismatch or a
    missing registered file is fatal because calibration changes final output.
    """
    calibrator = IsotonicCalibrator()
    is_wc = "world cup" in (competition or "").lower()

    if is_wc:
        calibrator.load(str(verified_artifact_path("calibrator_wc")))
        if calibrator.is_fitted and calibrator.training_sample_count >= 20:
            logger.info(
                "Pipeline: using WC calibrator (%d samples)",
                calibrator.training_sample_count,
            )
            return calibrator

    # Fallback: main calibrator
    calibrator = IsotonicCalibrator()
    calibrator.load(str(verified_artifact_path("calibrator")))
    return calibrator


def _count_market_providers(result: PredictionResult) -> int:
    """Extract the number of market data providers from a PredictionResult.

    Market data can come from:
      - web-search-consensus: sample_bookmakers=N (typically 5-11)
      - apifootball.com: 1 provider
      - The Odds API: 1 provider
      - manual-odds: 1 provider (last resort)

    Returns 0 if no market data was loaded at all.
    """
    market_probs = result.market_probs or {}
    # web-search-consensus provides explicit bookmaker count
    sample = market_probs.get("sample_bookmakers", 0)
    if sample > 0:
        return int(sample)
    # Single-provider sources
    if market_probs:
        return 1
    # Check source_status for cases where market was loaded but stored elsewhere
    src = result.source_status.get("market") if result.source_status else None
    if src is not None and getattr(src, 'status', '') == "used":
        return 1
    return 0


def _run_postflight_gate(
    result: PredictionResult,
    *,
    is_knockout: bool = False,
    market_required: bool = True,
) -> None:
    """Run post-flight verification gate on a completed prediction.

    Logs failures but does NOT block — the caller (CLI layer) decides
    whether to abort the DB write.  Library callers can inspect
    ``result.degraded_reasons`` for gate failures.
    """
    try:
        # Determine actual market provider count from pipeline artifacts
        market_prov_count = _count_market_providers(result)
        failures = postflight_check(
            probs={
                "home_win_prob": result.home_win_prob,
                "draw_prob": result.draw_prob,
                "away_win_prob": result.away_win_prob,
            },
            all_components_run=len(result.components_used),
            market_applied=result.market_applied,
            market_provider_count=market_prov_count,
            market_required=market_required,
            calibration_applied=result.calibration_applied,
            is_knockout=is_knockout,
            elo_gap=result.elo_gap,
        )
        if failures:
            for f in failures:
                logger.warning(
                    "Post-flight gate [%s] %s: %s",
                    f.severity, f.gate, f.message,
                )
                if f.severity == "error":
                    result.degraded_reasons.append(DegradedReason(
                        source=f"postflight_gate:{f.gate}",
                        reason=f.message,
                        severity="error",
                    ))
                else:
                    result.degraded_reasons.append(DegradedReason(
                        source=f"postflight_gate:{f.gate}",
                        reason=f.message,
                        severity="warning",
                    ))
    except Exception as exc:
        logger.warning("Post-flight gate check failed: %s", exc)


def _resolve_weibull_scenario_action(
    *,
    weibull_probs: dict[str, float] | None,
    base_weight: float,
    elo_gap: float | None,
    stage: str,
    market_probs: dict[str, Any] | None,
    total_xg: float | None,
    log_label: str = "",
) -> tuple[dict[str, Any], float]:
    """Classify Weibull output and return the action plus effective weight."""
    default = {"scenario": "normal", "action": "normal"}
    if weibull_probs is None or base_weight <= 0:
        return default, 0.0

    try:
        scenario = classify_weibull_scenario(
            weibull_probs=weibull_probs,
            elo_gap=elo_gap,
            is_knockout=_is_ko_stage(stage) if stage else False,
            market_probs=market_probs,
            total_xg=total_xg,
        )
        action = resolve_weibull_action(scenario, weibull_weight=base_weight)
        effective_weight = float(action["effective_weight"])
        if action["action"] in ("skip", "shadow"):
            logger.info(
                "Weibull scenario%s: %s -> %s (weight %.4f->%.4f): %s",
                f" ({log_label})" if log_label else "",
                scenario["scenario"],
                action["action"],
                base_weight,
                effective_weight,
                action["reason"],
            )
        return action, effective_weight
    except Exception as exc:
        logger.warning(
            "Weibull scenario classification failed%s; using full weight: %s",
            f" ({log_label})" if log_label else "",
            exc,
        )
        fallback = {
            "scenario": "normal",
            "action": "normal",
            "reason": "classification_failed_using_full_weight",
            "error": str(exc),
        }
        return fallback, float(base_weight)


def _extract_component_triplet(probs: dict[str, Any] | None) -> dict[str, float] | None:
    """Return normalized ``home/draw/away`` probabilities from mixed key styles."""
    if not isinstance(probs, dict):
        return None

    def first(*keys: str) -> float | None:
        for key in keys:
            if key in probs and probs[key] is not None:
                return float(probs[key])
        return None

    home = first("home", "home_win", "home_win_prob", "home_prob")
    draw = first("draw", "draw_prob")
    away = first("away", "away_win", "away_win_prob", "away_prob")
    if home is None or draw is None or away is None:
        return None
    home = max(home, 0.0)
    draw = max(draw, 0.0)
    away = max(away, 0.0)
    total = home + draw + away
    if total <= 0:
        return None
    return {
        "home": home / total,
        "draw": draw / total,
        "away": away / total,
    }


def _build_stacking_component_probs(
    *,
    dc_pred: dict[str, Any],
    enhancer_pred: dict[str, Any] | None,
    elo_pred: Any | None,
    pi_pred: dict[str, Any] | None,
    weibull_pred: dict[str, Any] | None,
    negbin_probs: dict[str, Any] | None,
    market_probs: dict[str, Any] | None,
) -> dict[str, dict[str, float]]:
    """Build canonical component probabilities for the stacking learner."""
    components: dict[str, dict[str, float]] = {}
    for name, raw in (
        ("dixon_coles", dc_pred),
        ("enhancer", enhancer_pred),
        ("negbin", negbin_probs),
        ("weibull", weibull_pred),
        ("pi_rating", pi_pred),
        ("market", market_probs),
    ):
        triplet = _extract_component_triplet(raw)
        if triplet is not None:
            components[name] = triplet

    if elo_pred is not None:
        components["elo"] = {
            "home": float(elo_pred.home_win_prob),
            "draw": float(elo_pred.draw_prob),
            "away": float(elo_pred.away_win_prob),
        }
    return components


class PredictionPipeline:
    """Unified prediction pipeline for WC26 match predictions.

    Usage:
        pipeline = PredictionPipeline()
        result = await pipeline.predict_match(
            home_team="Argentina",
            away_team="Brazil",
            competition="FIFA World Cup 2026",
            is_neutral=True,
        )
        logger.info(result.home_win_prob, result.draw_prob, result.away_win_prob)
    """

    def __init__(self) -> None:
        self._dc: DixonColesModel | None = None
        self._enhancer: TabularMatchEnhancer | None = None
        self._elo: EloRatingSystem = EloRatingSystem()
        self._pi: PiRatingWrapper = PiRatingWrapper()
        self._weibull: WeibullWrapper = WeibullWrapper()
        self._market: MarketCalibrator | None = None

    # ── Factory Methods ─────────────────────────────────────

    @classmethod
    def from_artifacts(cls, mode: str = "full") -> "PredictionPipeline":
        """Create a pipeline wired for artifact-based prediction.

        Loads pre-trained models from backend/artifacts/ — NO .fit() calls,
        NO DB required. Synchronous, ~1-3 seconds.

        Args:
            mode: "baseline" (DC only), "standard" (DC+Enhancer+Elo),
                  "full" (DC+Enhancer+Elo+Pi), "research-full" (+Weibull).

        Usage:
            pipeline = PredictionPipeline.from_artifacts(mode="full")
            result = pipeline.predict_sync("Qatar", "Switzerland",
                                           "FIFA World Cup 2026", is_neutral=True)
        """
        from app.services.prediction_core import (
            _load_dc as _load_dc_artifact,
            _load_enhancer as _load_enhancer_artifact,
            _load_elo as _load_elo_artifact,
            _load_pi as _load_pi_artifact,
            _load_training_df as _load_training_df_artifact,
            _try_load_weibull as _try_load_weibull_artifact,
        )
        from app.services.prediction_timer import PredictionTimer

        timer = PredictionTimer()
        pipeline = cls()
        pipeline._mode = mode
        pipeline._artifact_timer = timer
        pipeline._artifact_bundle = active_bundle_provenance()

        # ── Load training DataFrame ──
        pipeline._training_df = _load_training_df_artifact(timer)
        pipeline._match_date = pipeline._training_df["match_date"].max().to_pydatetime()

        # ── Load DC (required for all modes) ──
        pipeline._dc = _load_dc_artifact(timer)

        # ── Load Enhancer + Elo (standard+) ──
        if mode in ("standard", "full", "research-full"):
            pipeline._enhancer = _load_enhancer_artifact(timer)
            pipeline._elo = _load_elo_artifact(timer)

        # ── Load Pi (full+) ──
        if mode in ("full", "research-full"):
            pipeline._pi = _load_pi_artifact(timer)

        # ── Load Weibull (standard+, optional) ──
        if mode in ("standard", "full", "research-full"):
            pipeline._weibull = _try_load_weibull_artifact(timer)

        loaded = ["dc"]
        if hasattr(pipeline, "_enhancer") and pipeline._enhancer is not None:
            loaded.append("enhancer")
        if hasattr(pipeline, "_elo") and pipeline._elo is not None:
            loaded.append("elo")
        if hasattr(pipeline, "_pi") and pipeline._pi is not None:
            loaded.append("pi")
        if hasattr(pipeline, "_weibull") and pipeline._weibull is not None:
            loaded.append("weibull")

        logger.info(
            "PredictionPipeline.from_artifacts(mode=%s) — loaded: %s",
            mode, loaded,
        )
        return pipeline

    # ── Shared Fusion Helpers (V4.3.0 S7: delegates to core.engine) ─

    _fuse_dc_enhancer_adaptive = staticmethod(fuse_dc_enhancer_adaptive)
    _enforce_draw_floor = staticmethod(enforce_draw_floor)

    # ── Public API ──────────────────────────────────────────

    async def predict_match(
        self,
        home_team: str,
        away_team: str,
        competition: str,
        *,
        is_neutral: bool = False,
        mode: str | None = None,
        as_of: datetime | None = None,
        match_id: str = "",
        match_date: str | datetime | None = None,
        stage: str = "",
        venue: str | None = None,
        save_snapshot: bool = True,
        enable_weather: bool = True,
        enable_market: bool = True,
        require_full_context: bool = False,
        **legacy_callbacks: Any,
    ) -> PredictionResult:
        """Async compatibility wrapper around the canonical sync core.

        The former async implementation trained and fused a second model path.
        It was removed because it could not stay behaviorally identical to the
        artifact pipeline. Callers needing async execution now run the one
        canonical implementation in a worker thread.
        """
        unsupported = [key for key, value in legacy_callbacks.items() if value is not None]
        if unsupported:
            raise ValueError(
                "DB callback prediction was retired; use canonical_prediction_runner. "
                f"Unsupported callbacks: {', '.join(sorted(unsupported))}"
            )
        effective_mode = mode if mode in {"baseline", "standard", "full", "research-full"} else None
        effective_match_date = match_date or as_of
        return await asyncio.to_thread(
            self.predict_sync,
            home_team,
            away_team,
            competition,
            is_neutral=is_neutral,
            mode=effective_mode,
            match_id=match_id,
            match_date=effective_match_date,
            stage=stage,
            venue=venue,
            save_snapshot=save_snapshot,
            enable_weather=enable_weather,
            enable_market=enable_market,
            require_full_context=require_full_context,
        )

    async def predict(
        self,
        home_team: str,
        away_team: str,
        competition: str,
        **kwargs: Any,
    ) -> PredictionResult:
        """Deprecated alias for :meth:`predict_match`."""
        return await self.predict_match(home_team, away_team, competition, **kwargs)

    # ── Artifact-based prediction (sync, no DB) ───────────────

    def predict_sync(
        self,
        home_team: str,
        away_team: str,
        competition: str,
        *,
        is_neutral: bool = False,
        mode: str | None = None,
        match_id: str = "",
        match_date: str | datetime | None = None,
        stage: str = "",
        venue: str | None = None,
        save_snapshot: bool = True,
        enable_weather: bool = True,
        enable_market: bool = True,
        require_full_context: bool = False,
    ) -> PredictionResult:
        """Run artifact-based prediction synchronously. No DB required.

        Uses pre-loaded models from ``from_artifacts()``.
        Returns a fully-populated ``PredictionResult``.

        Args:
            home_team: Home team name (must match training data).
            away_team: Away team name.
            competition: Competition name (e.g. "FIFA World Cup 2026").
            is_neutral: True for neutral-venue matches.
            mode: Override the mode set in ``from_artifacts()``.
            match_id: Optional DB match id for closed-loop traceability.
            match_date: Optional kickoff/as-of date; defaults to artifact max date.
            stage: Explicit competition stage. Required for reliable knockout
                weights; DB lookup is only a compatibility fallback.
            venue: Optional venue name for weather lookup.
            save_snapshot: Persist a pre-match snapshot when true.
            enable_weather: Fetch weather context when true.
            enable_market: Fetch market consensus in shadow mode when true.
            require_full_context: Enforce the strict enhanced contract. Requires
                real match_id, match_date, venue, market, and weather attempts.
        """
        if mode is None:
            mode = getattr(self, "_mode", "full")
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if require_full_context:
            _validate_required_sync_context(
                match_id=match_id,
                match_date=match_date,
                venue=venue,
                enable_weather=enable_weather,
                enable_market=enable_market,
            )

        if not hasattr(self, "_dc") or self._dc is None:
            raise RuntimeError(
                "Artifacts not loaded. "
                "Use PredictionPipeline.from_artifacts(mode=...) first."
            )

        from app.services.fusion_graph import FusionGraph, probs_dict_to_list
        from app.services.run_quality import RunQuality

        quality = RunQuality()
        component_probs: dict[str, dict[str, float]] = {}
        degraded_reasons: list[DegradedReason] = []
        source_status = _initial_source_status(
            enable_weather=enable_weather,
            enable_market=enable_market,
            require_full_context=require_full_context,
        )

        # ── Weight config ──
        stage = stage or (
            _lookup_wc_stage(home_team, away_team, match_id=match_id)
            if (home_team and away_team)
            else ""
        )
        wc = get_weight_config(competition, stage)
        fg = FusionGraph(blend_params={
            "dc_weight": wc.dc, "weibull_weight": wc.weibull,
            "elo_weight": wc.elo, "pi_weight": wc.pi,
        })
        fg.compute_effective_weights()

        # ── Venue context (P0-7) ──
        from app.services.venue_context import detect_venue_context
        venue_ctx = detect_venue_context(venue, home_team, away_team, is_neutral=is_neutral)
        effective_is_neutral = (
            is_neutral and venue_ctx.is_effectively_neutral
        ) if is_neutral else is_neutral
        if not venue_ctx.is_effectively_neutral:
            logger.info(
                "Venue context: %s vs %s at %s — effective home advantage %.0f%% for %s",
                home_team, away_team, venue, venue_ctx.effective_home_advantage * 100,
                venue_ctx.advantage_team,
            )
            for w in venue_ctx.warnings:
                logger.warning("  Venue: %s", w)

        artifact_match_date = self._match_date
        effective_match_date = _coerce_match_datetime(match_date) or datetime.now(timezone.utc)
        training_df = self._training_df.loc[
            self._training_df["match_date"] < effective_match_date
        ].copy()
        if training_df.empty:
            raise RuntimeError(
                "No national-team feature history exists before the prediction cutoff"
            )
        kickoff_at = (
            effective_match_date.isoformat()
            if hasattr(effective_match_date, "isoformat")
            else str(effective_match_date)
        )
        source_status["match_context"] = _match_context_status(
            match_id=match_id,
            match_date=match_date,
            venue=venue,
            require_full_context=require_full_context,
        )

        # ── 1. Dixon-Coles ──
        quality.model_components["dixon_coles"] = "loaded_from_artifact"
        dc_pred = self._dc.predict_match(home_team, away_team, is_neutral_venue=effective_is_neutral)
        component_probs["dixon_coles"] = {
            "home": dc_pred["home_win_prob"],
            "draw": dc_pred["draw_prob"],
            "away": dc_pred["away_win_prob"],
        }
        fused: dict[str, float] = dict(dc_pred)

        # ── 2. Tabular Enhancer (standard+) ──
        has_enhancer = hasattr(self, "_enhancer") and self._enhancer is not None
        if mode in ("standard", "full", "research-full") and has_enhancer:
            quality.model_components["tabular_enhancer"] = "loaded_from_artifact"
            enh_weight = _default_competition_weight(competition)
            enh_pred = self._enhancer.predict_match(
                home_team=home_team, away_team=away_team,
                match_date=effective_match_date, competition_weight=enh_weight,
                is_neutral_venue=effective_is_neutral, training_df=training_df,
            )
            component_probs["enhancer"] = {
                "home": enh_pred["home_win_prob"],
                "draw": enh_pred["draw_prob"],
                "away": enh_pred["away_win_prob"],
            }
            before_step1 = {
                "dixon_coles": probs_dict_to_list(component_probs["dixon_coles"]),
                "enhancer": probs_dict_to_list(component_probs["enhancer"]),
            }

            # ── DC+Enhancer: prepare component probs, then delegate to engine ──
            dc_probs_std = {
                "home_win_prob": component_probs["dixon_coles"]["home"],
                "draw_prob": component_probs["dixon_coles"]["draw"],
                "away_win_prob": component_probs["dixon_coles"]["away"],
            }
            enh_probs_std = {
                "home_win_prob": enh_pred["home_win_prob"],
                "draw_prob": enh_pred["draw_prob"],
                "away_win_prob": enh_pred["away_win_prob"],
            }
            # Build the enhancer-only fused result for metadata tracking
            fused_long, max_div_sync, direction_conflict, dc_w_ef = \
                self._fuse_dc_enhancer_adaptive(dc_probs_std, enh_probs_std, wc.dc)
            fused = dict(fused_long)
            component_probs["dixon_coles+enhancer"] = {
                "home": fused["home_win_prob"],
                "draw": fused["draw_prob"],
                "away": fused["away_win_prob"],
            }
            step_label = f"base_weight={wc.dc}"
            if max_div_sync > 20 and direction_conflict:
                step_label += f" (direction-conflict override, divergence={max_div_sync:.1f}pp)"
            elif max_div_sync > 20:
                step_label += f" (adaptive dc={dc_w_ef:.2f}, divergence={max_div_sync:.1f}pp)"
            fg.add_step("dc+enhancer", step_label, before_step1,
                        [fused["home_win_prob"], fused["draw_prob"], fused["away_win_prob"]])
        else:
            # No enhancer — fused starts as DC probs
            fused = dict(dc_pred)
            max_div_sync = 0.0
            direction_conflict = False
            dc_w_ef = wc.dc

        # ── 2.4. Weibull (standard+) ──
        has_weibull = hasattr(self, "_weibull") and self._weibull is not None
        wb_pred = None
        if mode in ("standard", "full", "research-full") and has_weibull:
            quality.model_components["weibull"] = "loaded_from_artifact"
            try:
                wb_pred = self._weibull.predict(home_team, away_team, effective_is_neutral)
                if wb_pred is not None:
                    component_probs["weibull"] = {
                        "home": wb_pred.get("home_win_prob", wb_pred.get("home", 0)),
                        "draw": wb_pred.get("draw_prob", wb_pred.get("draw", 0)),
                        "away": wb_pred.get("away_win_prob", wb_pred.get("away", 0)),
                    }
            except Exception as exc:
                logger.warning(f"Weibull prediction failed: {exc}")

        # ── 2.5. Elo (standard+) ──
        has_elo = hasattr(self, "_elo") and self._elo is not None
        elo_pred = None
        if mode in ("standard", "full", "research-full") and has_elo:
            quality.model_components["elo"] = "loaded_from_artifact"
            elo_pred = self._elo.predict(
                home_team, away_team, is_neutral=effective_is_neutral,
                competition_weight=_default_competition_weight(competition), competition=competition,
            )
            component_probs["elo"] = {
                "home": elo_pred.home_win_prob,
                "draw": elo_pred.draw_prob,
                "away": elo_pred.away_win_prob,
            }

        market_probs_data: dict[str, Any] | None = None
        # ── 2.4b Weibull Scenario Rules (P1-2 Phase 2) ──
        # Runs after Elo to use elo_gap. Market data is fetched later, so this
        # pre-fusion guard intentionally runs without market support.
        weibull_scenario_result, weibull_effective_weight_sync = _resolve_weibull_scenario_action(
            weibull_probs=wb_pred,
            base_weight=wc.weibull if has_weibull and wb_pred is not None else 0.0,
            elo_gap=float(elo_pred.rating_gap) if elo_pred else None,
            stage=stage,
            market_probs=market_probs_data,
            total_xg=float(dc_pred.get("home_xg", 0)) + float(dc_pred.get("away_xg", 0)),
            log_label="sync",
        )

        # ── 2.6. Pi-Rating (full+) ──
        has_pi = hasattr(self, "_pi") and self._pi is not None
        pi_pred_for_core = None
        if mode in ("full", "research-full") and has_pi:
            quality.model_components["pi_rating"] = "loaded_from_artifact"
            try:
                pi_pred = self._pi.predict(home_team, away_team, effective_is_neutral)
                pi_pred_for_core = pi_pred
                component_probs["pi_rating"] = {
                    "home": pi_pred["home_win_prob"],
                    "draw": pi_pred["draw_prob"],
                    "away": pi_pred["away_win_prob"],
                }
            except Exception as exc:
                quality.model_components["pi_rating"] = "failed"
                quality.mark_degraded(f"Pi-Rating failed: {exc}")
                degraded_reasons.append(DegradedReason(
                    source="pi_rating", reason="fitting_failed",
                    severity="warning", detail=str(exc),
                ))

        # ── 3. Core Fusion: NegBin → Weibull → Elo → Pi (shared kernel) ──
        kernel_result_sync = run_prediction_kernel_from_components(
            home_team=home_team,
            away_team=away_team,
            competition=competition,
            stage=stage,
            is_neutral=effective_is_neutral,
            dc_pred=dc_pred,
            dc_weight=wc.dc,
            enhancer_probs=enh_probs_std if has_enhancer else None,
            weibull_probs=wb_pred,
            weibull_weight=weibull_effective_weight_sync,
            elo_pred=elo_pred if has_elo else None,
            elo_weight=wc.elo if has_elo else 0.0,
            pi_pred=pi_pred_for_core if has_pi else None,
            pi_weight=wc.pi if has_pi and pi_pred_for_core is not None else 0.0,
        )
        core = kernel_result_sync.core_fusion
        fused = dict(core.probs)
        negbin_applied = core.negbin_applied

        # Populate component_probs for downstream consumers (snapshot, learning)
        if core.negbin_applied and core.negbin_probs is not None:
            component_probs["negbin"] = dict(core.negbin_probs)

        # FusionGraph: record Weibull/Elo/Pi steps if they were applied
        if has_weibull and core.weibull_applied:
            fg.add_step("+weibull", f"wb_weight={wc.weibull}",
                        {"prev": probs_dict_to_list(fused)},
                        probs_dict_to_list(component_probs.get("weibull", {})))
        if has_elo and elo_pred is not None:
            fg.add_step("+elo", f"elo_weight={wc.elo}",
                        {"prev": probs_dict_to_list(fused)},
                        probs_dict_to_list(component_probs.get("elo", {})))
        if has_pi and pi_pred_for_core is not None:
            fg.add_step("+pi", f"pi_weight={wc.pi}",
                        {"prev": probs_dict_to_list(fused)},
                        probs_dict_to_list(component_probs.get("pi_rating", {})))

        # ── 4.5. Match Importance / Tournament Context (V4.2.1) ──
        # Mirror of predict_match_full.py Step 4.5 + predict_match() Step 9.5.
        motivation_result_sync: object = None
        is_wc_sync = "world cup" in (competition or "").lower()
        if is_wc_sync:
            try:
                from app.services.group_standings import GroupStandingsService
                from app.services.match_importance import MatchImportanceCalculator
                standings_svc = GroupStandingsService()
                calc = MatchImportanceCalculator()
                motivation_result_sync = calc.analyze(home_team, away_team, standings_svc)

                if motivation_result_sync.matchday == 3:
                    home_adj = motivation_result_sync.home_win_adj
                    draw_adj = motivation_result_sync.draw_adj
                    away_adj = motivation_result_sync.away_win_adj

                    fused["home_win_prob"] = max(0.02, fused["home_win_prob"] + home_adj)
                    fused["draw_prob"] = max(0.02, fused["draw_prob"] + draw_adj)
                    fused["away_win_prob"] = max(0.02, fused["away_win_prob"] + away_adj)
                    total = fused["home_win_prob"] + fused["draw_prob"] + fused["away_win_prob"]
                    if total > 0:
                        fused["home_win_prob"] /= total
                        fused["draw_prob"] /= total
                        fused["away_win_prob"] /= total

                    logger.info(
                        "predict_sync MOTIVATION: [%s] Group %s MD%d | "
                        "adj: H%+.3f D%+.3f A%+.3f | collusion=%.2f",
                        motivation_result_sync.match_type.value,
                        motivation_result_sync.group_name,
                        motivation_result_sync.matchday,
                        home_adj, draw_adj, away_adj,
                        motivation_result_sync.collusion_risk,
                    )
                else:
                    logger.info(
                        "predict_sync MOTIVATION: MD%d — skipped (only MD3 active)",
                        motivation_result_sync.matchday,
                    )
            except Exception as exc:
                logger.warning("predict_sync MOTIVATION: skipped (%s)", exc)

        # ── 6. Fusion diagnostics ──
        fg.compute_disagreement(component_probs)

        # ── 7. Renormalize ──
        total = fused["home_win_prob"] + fused["draw_prob"] + fused["away_win_prob"]
        if abs(total - 1.0) > 0.001:
            fused["home_win_prob"] /= total
            fused["draw_prob"] /= total
            fused["away_win_prob"] /= total

        # ── 8. Injury signals (best-effort) ──
        injury_signals_count = 0
        injury_data_available = False
        try:
            from app.services.injury_data import InjuryDataService, fuse_injury_signals
            injury_svc = InjuryDataService()
            injury_records = injury_svc.load()
            if injury_records:
                injury_data_available = True
                relevant = [
                    r for r in injury_records
                    if r.team_name.lower() in (home_team.lower(), away_team.lower())
                ]
                if relevant:
                    injury_dicts = [
                        {
                            "team_name": r.team_name,
                            "player_name": r.player_name,
                            "status": r.status,
                            "confidence": r.confidence,
                        }
                        for r in relevant
                    ]
                    fused = fuse_injury_signals(
                        fused,
                        injury_dicts,
                        home_team=home_team,
                        away_team=away_team,
                    )
                    injury_signals_count = len(injury_dicts)
                    quality.model_components["injury_data"] = "applied"
                    source_status["injuries"] = SourceStatus(
                        status="used",
                        reason="relevant_records_applied",
                        detail=f"records={injury_signals_count}",
                        attempted=True,
                    )
                else:
                    source_status["injuries"] = SourceStatus(
                        status="unavailable",
                        reason="no_relevant_records",
                        detail=f"loaded_records={len(injury_records)}",
                        attempted=True,
                    )
            else:
                source_status["injuries"] = SourceStatus(
                    status="unavailable",
                    reason="empty_dataset",
                    attempted=True,
                )
        except Exception as exc:
            logger.warning(f"Injury signals skipped: {exc}")
            source_status["injuries"] = SourceStatus(
                status="failed",
                reason="load_failed",
                detail=str(exc),
                attempted=True,
            )
            degraded_reasons.append(DegradedReason(
                source="injuries",
                reason="load_failed",
                severity="warning",
                detail=str(exc),
            ))

        # ── 9. News signals (best-effort) ──
        news_signals_count = 0
        news_signals_available = False
        news_signal_ids: list[str] = []
        approved_signals: list[dict[str, Any]] = []
        signal_risk_tags: list[str] = []
        try:
            from app.services.signal_adjuster_sync import apply_signal_adjustments, load_approved_signals
            approved = load_approved_signals(
                home_team,
                away_team,
                match_id=match_id,
                as_of_time=now_utc,
                kickoff_at=kickoff_at,
                db_path=current_sync_sqlite_path(),
            )
            if approved:
                approved_signals = [item for item in approved if isinstance(item, dict)]
                news_signals_available = True
                news_signals_count = len(approved)
                news_signal_ids = [s.get("id", "") for s in approved if s.get("id")]
                (
                    fused["home_win_prob"],
                    fused["draw_prob"],
                    fused["away_win_prob"],
                    signal_risk_tags,
                ) = apply_signal_adjustments(
                    home_prob=fused["home_win_prob"],
                    draw_prob=fused["draw_prob"],
                    away_prob=fused["away_win_prob"],
                    home_team=home_team,
                    away_team=away_team,
                    match_id=match_id,
                    signals=approved,
                )
                quality.model_components["news_signals"] = "applied"
                source_status["news"] = SourceStatus(
                    status="used",
                    reason="approved_signals_applied",
                    detail=f"records={news_signals_count}",
                    attempted=True,
                )
            else:
                source_status["news"] = SourceStatus(
                    status="unavailable",
                    reason="no_approved_signals",
                    attempted=True,
                )
        except Exception as exc:
            logger.warning(f"News signals skipped: {exc}")
            source_status["news"] = SourceStatus(
                status="failed",
                reason="load_failed",
                detail=str(exc),
                attempted=True,
            )
            degraded_reasons.append(DegradedReason(
                source="news",
                reason="load_failed",
                severity="warning",
                detail=str(exc),
            ))

        # ── 10. Weather data (Open-Meteo API via WeatherService) ──
        weather_data: dict[str, Any] | None = None
        weather_available = False
        weather_risk_tags: list[str] = []
        if enable_weather:
            try:
                from app.services.weather_service import WeatherService
                weather_svc = WeatherService()
                weather_data = weather_svc.get_weather_for_match_sync(
                    venue=venue,
                    home_team=home_team,
                    away_team=away_team,
                )
                if weather_data and weather_data.get("forecast_available"):
                    weather_available = True
                    weather_risk_tags = weather_svc.weather_impact_tags(weather_data)
                    if weather_risk_tags:
                        signal_risk_tags.extend(weather_risk_tags)
                    quality.model_components["weather"] = "loaded"
                    logger.info(
                        f"Weather: {weather_data.get('weather_description', '?')} "
                        f"{weather_data.get('temperature_c', '?')}°C "
                        f"tags={weather_risk_tags}"
                    )
                    source_status["weather"] = SourceStatus(
                        status="used",
                        reason="forecast_loaded",
                        detail=str(weather_data.get("weather_description", "")),
                        attempted=True,
                        required=require_full_context,
                    )
                else:
                    quality.model_components["weather"] = "unavailable"
                    weather_reason = (
                        weather_data.get("reason")
                        or weather_data.get("degraded_reason")
                        if weather_data
                        else "no_data"
                    )
                    source_status["weather"] = SourceStatus(
                        status="unavailable",
                        reason=str(weather_reason or "forecast_unavailable"),
                        detail=str(weather_data or "no data"),
                        attempted=True,
                        required=require_full_context,
                    )
                    degraded_reasons.append(DegradedReason(
                        source="weather",
                        reason="forecast_unavailable",
                        severity="warning",
                        detail=weather_data.get("degraded_reason", "") if weather_data else "no data",
                    ))
            except Exception as exc:
                logger.warning(f"Weather fetch failed: {exc}")
                source_status["weather"] = SourceStatus(
                    status="failed",
                    reason="fetch_failed",
                    detail=str(exc),
                    attempted=True,
                    required=require_full_context,
                )
                degraded_reasons.append(DegradedReason(
                    source="weather",
                    reason="fetch_failed",
                    severity="warning",
                    detail=str(exc),
                ))
        else:
            quality.model_components["weather"] = "disabled"
            source_status["weather"] = SourceStatus(
                status="skipped",
                reason="disabled_by_flag",
                attempted=False,
                required=require_full_context,
            )

        # ── 11. Market calibration (real API call) ──
        pre_market_probs = dict(fused)
        market_applied = False
        market_weight_used = 0.0
        divergence = 0.0
        market_probs = None
        market_probs_data = None
        market_available = False
        if enable_market:
            try:
                market = get_calibrator(shadow_mode=True)
                market_probs_data = _run_async_in_thread(
                    market.fetch_market_probs(home_team, away_team,
                        _default_competition_weight(competition), competition=competition)
                )
                if market_probs_data:
                    market_available = True
                    quality.model_components["market"] = "loaded"
                    market_result = market.calibrate(
                        {"home_win_prob": fused["home_win_prob"],
                         "draw_prob": fused["draw_prob"],
                         "away_win_prob": fused["away_win_prob"]},
                        market_probs_data,
                        sample_size=len(training_df),
                    )
                    if market_result.get("market_applied"):
                        fused["home_win_prob"] = market_result["home_win_prob"]
                        fused["draw_prob"] = market_result["draw_prob"]
                        fused["away_win_prob"] = market_result["away_win_prob"]
                        market_applied = True
                        market_weight_used = float(market_result.get("market_weight_used", 0))
                        divergence = float(market_result.get("divergence", 0))
                    market_probs = market_probs_data
                    if market_result.get("risk_tags"):
                        signal_risk_tags.extend(market_result["risk_tags"])
                    logger.info(
                        f"Market: H={market_probs_data.get('home_prob',0):.3f} "
                        f"D={market_probs_data.get('draw_prob',0):.3f} "
                        f"A={market_probs_data.get('away_prob',0):.3f}"
                    )
                    source_status["market"] = SourceStatus(
                        status="used",
                        reason="shadow_mode_loaded",
                        detail=str(market_probs_data.get("provider", "")),
                        attempted=True,
                        required=require_full_context,
                    )
                else:
                    # ── Fallback: sync_provider (checks _manual_odds.json first) ──
                    # MarketCalibrator goes apifootball.com → The Odds API (both
                    # often dead). sync_provider checks manual web-verified odds
                    # BEFORE hitting APIs, so it succeeds even when APIs are down.
                    try:
                        from app.services.market.sync_provider import (
                            fetch_market_consensus_sync,
                        )
                        market_probs_data = fetch_market_consensus_sync(
                            home_team, away_team, competition
                        )
                    except Exception:
                        market_probs_data = None

                    if market_probs_data:
                        market_available = True
                        quality.model_components["market"] = "loaded"
                        market_result = market.calibrate(
                            {"home_win_prob": fused["home_win_prob"],
                             "draw_prob": fused["draw_prob"],
                             "away_win_prob": fused["away_win_prob"]},
                            market_probs_data,
                            sample_size=len(training_df),
                        )
                        if market_result.get("market_applied"):
                            fused["home_win_prob"] = market_result["home_win_prob"]
                            fused["draw_prob"] = market_result["draw_prob"]
                            fused["away_win_prob"] = market_result["away_win_prob"]
                            market_applied = True
                            market_weight_used = float(market_result.get("market_weight_used", 0))
                            divergence = float(market_result.get("divergence", 0))
                        market_probs = market_probs_data
                        if market_result.get("risk_tags"):
                            signal_risk_tags.extend(market_result["risk_tags"])
                        logger.info(
                            f"Market (manual fallback): "
                            f"H={market_probs_data.get('home_prob',0):.3f} "
                            f"D={market_probs_data.get('draw_prob',0):.3f} "
                            f"A={market_probs_data.get('away_prob',0):.3f}"
                        )
                        source_status["market"] = SourceStatus(
                            status="used",
                            reason="manual_odds_fallback",
                            detail=str(market_probs_data.get("provider", "")),
                            attempted=True,
                            required=require_full_context,
                        )
                    else:
                        quality.model_components["market"] = "unavailable"
                        source_status["market"] = SourceStatus(
                            status="unavailable",
                            reason="no_market_data_for_match",
                            attempted=True,
                            required=require_full_context,
                        )
                        degraded_reasons.append(DegradedReason(
                            source="market_calibration",
                            reason="no_odds_for_match",
                            severity="warning",
                        ))
            except Exception as exc:
                logger.warning(f"Market calibration failed: {exc}")
                source_status["market"] = SourceStatus(
                    status="failed",
                    reason="fetch_failed",
                    detail=str(exc),
                    attempted=True,
                    required=require_full_context,
                )
                degraded_reasons.append(DegradedReason(
                    source="market_calibration",
                    reason="fetch_failed",
                    severity="warning",
                    detail=str(exc),
                ))
        else:
            quality.model_components["market"] = "disabled"
            source_status["market"] = SourceStatus(
                status="skipped",
                reason="disabled_by_flag",
                attempted=False,
                required=require_full_context,
            )

        # ── V4.7.0: Cross-validate with web consensus (always, not just on failure) ──
        if market_probs_data is not None:
            try:
                from app.services.market.sync_provider import _lookup_web_consensus
                web_sync = _lookup_web_consensus(home_team, away_team)
                if web_sync is not None and web_sync.get("sample_bookmakers", 0) >= 3:
                    api_n = market_probs_data.get("sample_bookmakers", 1)
                    web_n = web_sync.get("sample_bookmakers", 0)
                    if api_n < web_n:
                        logger.info(
                            "Market sync: cross-validated %d→%d bookmakers via web consensus",
                            api_n, web_n,
                        )
                        market_probs_data = web_sync
                        market_probs = web_sync  # keep snapshot var in sync
            except Exception:
                pass  # Best-effort cross-validation; non-fatal

        # ── 10.3 Market consensus gate (P1-1 Phase 2) ──
        market_consensus_result_sync: dict[str, Any] = {"checked": False, "triggered": False}
        effective_market_max_sync: float = wc.market_max
        if market_probs_data:
            effective_market_max_sync, market_consensus_result_sync = apply_market_consensus_gate(
                market_max_weight=wc.market_max,
                market_probs=market_probs_data,
            )
            if market_consensus_result_sync.get("triggered"):
                logger.info("Market consensus gate (sync): %s", market_consensus_result_sync.get("reason"))

        # ── 10.3b Dynamic market boost (V4.3.0: unified — engine.apply_market_boost) ──
        if market_probs_data and not market_applied:
            mb_result = apply_market_boost(
                fused=fused,
                market_probs=market_probs_data,
                market_max_weight=effective_market_max_sync,
                dc_enhancer_divergence_pp=max_div_sync,
                dc_enhancer_direction_conflict=direction_conflict,
                pre_market_probs=pre_market_probs,
            )
            if mb_result.market_applied:
                fused.update(mb_result.probs)
                market_applied = True
                market_weight_used = mb_result.market_weight_used
                divergence = mb_result.divergence
                if mb_result.boost_attenuated:
                    logger.info(
                        "Dynamic market boost attenuated (boost=%.3f)",
                        mb_result.market_weight_used - wc.market_max,
                    )
                logger.info(
                    "Dynamic market boost (sync): divergence=%.1f%%, weight=%.2f",
                    mb_result.divergence * 100, mb_result.market_weight_used,
                )

        # ── 10.4 Draw floor (V4.2.1) ──
        # Mirror of predict_match_full.py Step 6 draw floor enforcement.
        if is_wc_sync:
            draw_floor_fused, draw_floor_applied = self._enforce_draw_floor(fused)
            fused.update(draw_floor_fused)
            if draw_floor_applied:
                logger.info("Draw floor applied (sync): draw bumped to 12%%")

        # ── 10.5 Isotonic calibration (R4-C7: was disabled stub) ──
        calibration_applied = False
        calibration_monitor: dict[str, object] = {"enabled": False}
        try:
            calibrator = _load_isotonic_calibrator(competition)
            # P1-1: Apply WC calibrator even when market data is available.
            if calibrator is not None and calibrator.is_fitted:
                pre_cal = {
                    "home_win_prob": fused["home_win_prob"],
                    "draw_prob": fused["draw_prob"],
                    "away_win_prob": fused["away_win_prob"],
                }
                cal_result = calibrator.calibrate(pre_cal)
                fused["home_win_prob"] = cal_result["home_win_prob"]
                fused["draw_prob"] = cal_result["draw_prob"]
                fused["away_win_prob"] = cal_result["away_win_prob"]
                calibration_applied = True
                calibration_monitor = {
                    "enabled": True,
                    "sample_count": calibrator.training_sample_count,
                    "calibration_stats": calibrator.calibration_stats(),
                    "pre_calibration_probs": pre_cal,
                }
            else:
                if calibrator is None:
                    cal_reason = "skipped: calibrator not loaded"
                else:
                    cal_reason = (
                        f"calibrator not fitted (fitted={calibrator.is_fitted}, "
                        f"samples={calibrator.training_sample_count})"
                    )
                calibration_monitor = {
                    "enabled": False,
                    "reason": cal_reason,
                }
        except Exception as exc:
            logger.warning("Isotonic calibration failed — continuing without", exc_info=True)
            calibration_monitor = {
                "enabled": False,
                "reason": f"calibration exception: {exc}",
            }

        # ── 10.6 Score matrix fusion + calibration (V4.7) ──
        score_matrix_diag: dict[str, Any] = {
            "calibration_applied": False,
            "fusion_sources": [],
        }
        calibrated_top_scores: list[dict[str, Any]] | None = None
        calibrated_score_matrix: list[list[float]] | None = None
        source_score_matrices_sync: dict[str, list[list[float]]] = {}

        raw_score_matrix = dc_pred.get("score_matrix")
        if SCORE_MATRIX_CALIBRATION_ENABLED and raw_score_matrix:
            try:
                expanded_dc_matrix, _ = self._dc.predict_score_matrix(
                    home_team,
                    away_team,
                    is_neutral_venue=effective_is_neutral,
                    max_goals=10,
                )
                raw_score_matrix = expanded_dc_matrix.tolist()
                dc_home_xg = float(dc_pred.get("home_xg", 0))
                dc_away_xg = float(dc_pred.get("away_xg", 0))
                tau_rho = getattr(self._dc, "rho", -0.30) if hasattr(self._dc, "rho") else -0.30
                wb_score_matrix = None
                if hasattr(self, "_weibull") and self._weibull is not None:
                    wb_score_matrix = self._weibull.predict_score_matrix(
                        home_team, away_team, effective_is_neutral, max_goals=10,
                    )
                score_fusion = build_score_matrix_fusion(
                    raw_score_matrix=raw_score_matrix,
                    final_probs={
                        "home_win_prob": fused["home_win_prob"],
                        "draw_prob": fused["draw_prob"],
                        "away_win_prob": fused["away_win_prob"],
                    },
                    home_xg=dc_home_xg,
                    away_xg=dc_away_xg,
                    tau_rho=tau_rho,
                    weibull_score_matrix=wb_score_matrix,
                    max_goals=10,
                )
                calibrated_top_scores = score_fusion.top_scores
                calibrated_score_matrix = score_fusion.score_matrix
                score_matrix_diag = score_fusion.diagnostics
                source_score_matrices_sync = score_fusion.source_score_matrices
            except Exception as exc:
                logger.warning(
                    "Score matrix fusion failed (sync) — using raw DC: %s", exc
                )
                score_matrix_diag = {
                    "calibration_applied": False,
                    "error": str(exc),
                }

        # ── 10.7 KO draw guard (P0-2) ──
        ko_draw_guard_result: dict[str, Any] = {"checked": False, "triggered": False}
        # risk_tags initialized here so KO Draw Guard block below can append safely
        risk_tags: list[str] = []
        try:
            ko_draw_guard_result = check_ko_draw_guard(
                draw_prob=float(fused["draw_prob"]),
                stage=stage,
                total_xg=float(dc_pred.get("home_xg", 0)) + float(dc_pred.get("away_xg", 0)),
                market_draw_prob=(
                    float(market_probs_data["draw_prob"])
                    if market_probs_data and "draw_prob" in market_probs_data
                    else None
                ),
            )
            if ko_draw_guard_result.get("triggered"):
                logger.warning(
                    "KO draw guard triggered (sync): %s", ko_draw_guard_result.get("reason")
                )
                risk_tags.append("KO draw underestimation risk")
        except Exception as exc:
            logger.warning("KO draw guard check failed (sync) — continuing: %s", exc)

        # ── 10.7b KO post-calibration draw guard (P1-3 Phase 2) ──
        ko_post_cal_guard_result: dict[str, Any] = {"checked": False, "triggered": False}
        if calibration_applied and calibration_monitor.get("pre_calibration_probs"):
            try:
                pre_cal = calibration_monitor["pre_calibration_probs"]
                adjusted, ko_post_cal_guard_result = enforce_ko_draw_post_calibration(
                    pre_calibration_probs=pre_cal,
                    post_calibration_probs=fused,
                    stage=stage,
                    elo_gap=float(elo_pred.rating_gap) if elo_pred else None,
                    total_xg=float(dc_pred.get("home_xg", 0)) + float(dc_pred.get("away_xg", 0)),
                    market_draw_prob=(
                        float(market_probs_data["draw_prob"])
                        if market_probs_data and "draw_prob" in market_probs_data
                        else None
                    ),
                    model_disagreement=bool(ko_draw_guard_result.get("triggered")),
                )
                if ko_post_cal_guard_result.get("triggered"):
                    logger.warning(
                        "KO post-cal draw guard (sync): %s",
                        ko_post_cal_guard_result.get("reason"),
                    )
                    fused["home_win_prob"] = adjusted["home_win_prob"]
                    fused["draw_prob"] = adjusted["draw_prob"]
                    fused["away_win_prob"] = adjusted["away_win_prob"]
                    risk_tags.append("KO post-cal draw correction")
            except Exception as exc:
                logger.warning(
                    "KO post-cal draw guard failed (sync) — continuing: %s", exc
                )

        # ── 11. Pipeline status ──
        used_components = [
            c for c, s in quality.model_components.items()
            if s in ("loaded_from_artifact", "applied")
        ]
        expected = {
            "baseline": ["dixon_coles"],
            "standard": ["dixon_coles", "tabular_enhancer", "elo"],
            "full": ["dixon_coles", "tabular_enhancer", "elo", "pi_rating"],
            "research-full": ["dixon_coles", "tabular_enhancer", "elo", "pi_rating"],
        }.get(mode, [])

        all_loaded = all(
            quality.model_components.get(c) in ("loaded_from_artifact", "applied")
            for c in expected
        )
        if all_loaded:
            quality.pipeline_status = "full"
        elif len(used_components) >= 2:
            quality.pipeline_status = "degraded"
        else:
            quality.pipeline_status = "failed"

        if require_full_context:
            _apply_required_source_gate(
                source_status=source_status,
                degraded_reasons=degraded_reasons,
                quality=quality,
            )

        # ── 12. Risk tags ──
        # Preserve KO draw guard / post-cal tags appended above (lines ~2095, ~2125)
        risk_tags = list(dc_pred.get("risk_tags", [])) + signal_risk_tags + venue_ctx.risk_tags + risk_tags
        max_diff = fg.model_disagreement.get("max_home_diff", 0.0) if fg.model_disagreement else 0.0
        if max_diff > 0.30:
            risk_tags.append(f"high_model_disagreement_{max_diff:.2f}")

        # ── 13. Build PredictionResult ──
        components_used = list(used_components)
        if market_applied:
            components_used.append("market")
        if calibration_applied:
            components_used.append("calibration")

        # ── 10.8 A3: Stacking Meta-Learner (feature-flagged, V4.5) ──
        stacking_result: dict[str, Any] | None = None
        from app.core.stacking_features import STACKING_META_LEARNER_ENABLED
        if STACKING_META_LEARNER_ENABLED:
            from app.services.stacking_meta_learner import StackingMetaLearner

            _learner = StackingMetaLearner()
            _learner.load(str(verified_artifact_path("stacking_meta_learner")))
            if not _learner.is_fitted:
                raise RuntimeError("Enabled stacking artifact is not fitted")
            _stacked = _learner.predict_proba(component_probs, market_probs)
            stacking_result = {
                "applied": True,
                "pre_stacking_probs": dict(fused),
                "stacked_probs": _stacked,
                "training_samples": _learner.training_sample_count,
            }
            fused["home_win_prob"] = _stacked["home_win_prob"]
            fused["draw_prob"] = _stacked["draw_prob"]
            fused["away_win_prob"] = _stacked["away_win_prob"]
            components_used.append("stacking")
            logger.info(
                "A3 stacking applied (%d training samples)",
                _learner.training_sample_count,
            )

        # ── 10.9 B1: Weighted Conformal Prediction (feature-flagged, V4.5) ──
        conformal_result: dict[str, Any] | None = None
        from app.core.conformal_core import WEIGHTED_CONFORMAL_PREDICTION_ENABLED
        if WEIGHTED_CONFORMAL_PREDICTION_ENABLED:
            from app.services.conformal_predictor import WeightedConformalPredictor

            _predictor = WeightedConformalPredictor()
            _predictor.load(str(verified_artifact_path("conformal_predictor")))
            if not _predictor.is_fitted:
                raise RuntimeError("Enabled conformal artifact is not fitted")
            conformal_result = _predictor.predict(
                probs=fused,
                as_of=kickoff_at or now_utc,
            )
            _cp_probs = conformal_result["adjusted_probs"]
            fused["home_win_prob"] = _cp_probs[0]
            fused["draw_prob"] = _cp_probs[1]
            fused["away_win_prob"] = _cp_probs[2]
            components_used.append("conformal")
            logger.info(
                "B1 conformal prediction applied (set_size=%d, threshold=%.4f)",
                conformal_result["set_size"], conformal_result["threshold"],
            )

        # Score probabilities must describe the same final 1X2 distribution
        # after every nonlinear guard/calibrator/stacker has finished.
        if calibrated_score_matrix is not None:
            final_score_reconciliation = calibrate_score_matrix(
                raw_matrix=calibrated_score_matrix,
                final_probs={
                    "home_win_prob": fused["home_win_prob"],
                    "draw_prob": fused["draw_prob"],
                    "away_win_prob": fused["away_win_prob"],
                },
            )
            calibrated_score_matrix = final_score_reconciliation["calibrated_matrix"]
            calibrated_top_scores = final_score_reconciliation["top3_scores"]
            score_matrix_diag["final_outcome_reconciliation"] = {
                key: value
                for key, value in final_score_reconciliation.items()
                if key != "calibrated_matrix"
            }

        # Parameter provenance — traceable fingerprint of model state
        dc_provenance: dict[str, object] = {}
        try:
            dc_params_sorted = json.dumps(
                sorted(self._dc.attack_params.items()),
                sort_keys=True,
            ).encode()
            dc_provenance["dc_params_hash"] = hashlib.sha256(dc_params_sorted).hexdigest()
            dc_provenance["dc_teams"] = len(self._dc.attack_params)
        except Exception:
            dc_provenance["dc_params_hash"] = "unavailable"

        try:
            df_fp = (
                str(len(training_df)),
                str(training_df["match_date"].min()),
                str(training_df["match_date"].max()),
            )
            dc_provenance["training_df_fingerprint"] = hashlib.sha256(
                str(df_fp).encode()
            ).hexdigest()
            dc_provenance["training_rows"] = len(training_df)
        except Exception:
            dc_provenance["training_df_fingerprint"] = "unavailable"
            dc_provenance["training_rows"] = len(training_df) if training_df is not None else 0

        # Elo detail (available when standard+ mode)
        elo_detail: dict[str, object] = {}
        if elo_pred is not None:
            try:
                elo_detail = {
                    "k_factor": elo_pred.k_factor,
                    "home_elo": elo_pred.home_elo,
                    "away_elo": elo_pred.away_elo,
                    "rating_gap": elo_pred.rating_gap,
                    "draw_kappa": elo_pred.draw_kappa,
                }
            except AttributeError:
                pass

        completed_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = PredictionResult(
            home_team=home_team,
            away_team=away_team,
            competition=competition,
            match_id=match_id,
            is_neutral=effective_is_neutral,
            match_date=kickoff_at,
            stage=stage,
            home_win_prob=float(fused["home_win_prob"]),
            draw_prob=float(fused["draw_prob"]),
            away_win_prob=float(fused["away_win_prob"]),
            home_xg=float(dc_pred.get("home_xg", 0)),
            away_xg=float(dc_pred.get("away_xg", 0)),
            dc_probs={
                "home": float(component_probs.get("dixon_coles", {}).get("home", fused["home_win_prob"])),
                "draw": float(component_probs.get("dixon_coles", {}).get("draw", fused["draw_prob"])),
                "away": float(component_probs.get("dixon_coles", {}).get("away", fused["away_win_prob"])),
            },
            enhancer_probs=component_probs.get("enhancer") if "enhancer" in component_probs else None,
            elo_probs=component_probs.get("elo") if "elo" in component_probs else None,
            pi_probs=component_probs.get("pi_rating") if "pi_rating" in component_probs else None,
            weibull_probs=component_probs.get("weibull") if "weibull" in component_probs else None,
            market_probs=market_probs,
            home_elo=float(elo_detail.get("home_elo", 1500.0)),
            away_elo=float(elo_detail.get("away_elo", 1500.0)),
            elo_gap=float(elo_detail.get("rating_gap", 0.0)),
            top_scores=calibrated_top_scores if calibrated_top_scores is not None
                       else list(dc_pred.get("top3_scores", [])),
            score_matrix=calibrated_score_matrix if calibrated_score_matrix is not None
                         else list(dc_pred.get("score_matrix", [])),
            source_score_matrices=source_score_matrices_sync,
            weight_config=wc,
            mode="internal_research",
            as_of=completed_utc,
            generated_at=completed_utc,
            confidence=dc_pred.get("data_quality", "fitted"),
            risk_tags=risk_tags,
            confidence_penalty=float(dc_pred.get("confidence_penalty", 0.0)),
            components_used=components_used,
            missing_inputs=[dr.source for dr in degraded_reasons if dr.severity == "error"],
            degraded_reasons=degraded_reasons,
            pipeline_params={
                "dc_converged": True,
                "enhancer_rows": getattr(self._enhancer, "training_sample_count", 0) if has_enhancer else 0,
                "elo_matches": getattr(self._elo, "_match_count", 0) if has_elo else 0,
                "config_label": f"{wc.label} (DC{wc.dc:.0%}+Enh{wc.enhancer:.0%}+Elo{wc.elo:.0%}+Pi{wc.pi:.0%})",
                "training_rows": dc_provenance.get("training_rows", len(training_df)),
                "dc_params_hash": dc_provenance.get("dc_params_hash", "unavailable"),
                "training_df_fingerprint": dc_provenance.get("training_df_fingerprint", "unavailable"),
                "training_df_max_date": str(training_df["match_date"].max()) if training_df is not None else "",
                "training_df_role": "pre_cutoff_enhancer_feature_history",
                "artifact_training_max_date": str(artifact_match_date),
                "artifact_bundle": getattr(self, "_artifact_bundle", {}),
                "generation_started_at": now_utc,
                "require_full_context": require_full_context,
                "stage": stage,
                "is_neutral": effective_is_neutral,
                "venue": venue,
                "kickoff_at": kickoff_at,
                "pre_market_probs": pre_market_probs,
                "market_weight_used": market_weight_used,
                "calibration_applied": calibration_applied,
                "score_matrix_calibration": score_matrix_diag,
                "prediction_kernel": kernel_result_sync.provenance,
                "ko_draw_guard": ko_draw_guard_result,
                "ko_post_cal_guard": ko_post_cal_guard_result,
                "weibull_scenario": weibull_scenario_result,
                "market_consensus_gate": market_consensus_result_sync,
                "stacking_result": stacking_result,
                "conformal_result": conformal_result,
                "effective_weights": {
                    "dc_effective": round(wc.dc * (1 - wc.weibull) * (1 - wc.elo) * (1 - wc.pi), 6),
                    "enhancer_effective": round(wc.enhancer * (1 - wc.weibull) * (1 - wc.elo) * (1 - wc.pi), 6),
                    "weibull_effective": round(wc.weibull * (1 - wc.elo) * (1 - wc.pi), 6),
                    "elo_effective": round(wc.elo * (1 - wc.pi), 6),
                    "pi_effective": round(wc.pi, 6),
                    "_sum_to_1": True,
                },
            },
            source_status=source_status,
            active_events=approved_signals,
            market_applied=market_applied,
            market_weight_used=market_weight_used,
            divergence=divergence,
            weibull_applied=has_weibull and "weibull" in component_probs,
            negbin_applied=negbin_applied,
            negbin_probs=component_probs.get("negbin"),
            elo_detail=elo_detail,
            calibration_monitor=calibration_monitor,
            calibration_applied=calibration_applied,
            stacking_result=stacking_result,
            conformal_result=conformal_result,
        )

        # ── 15. Save pre-match snapshot (best-effort) ──
        if save_snapshot:
            _save_snapshot_sync(
                result=result, quality=quality, component_probs=component_probs,
                fg=fg, wc=wc,
                match_id=match_id,
                kickoff_at=kickoff_at,
                injury_signals_count=injury_signals_count,
                injury_data_available=injury_data_available,
                news_signals_count=news_signals_count,
                news_signals_available=news_signals_available,
                news_signal_ids=news_signal_ids,
                weather_available=weather_available,
                weather_data=weather_data,
                odds_available=market_available,
                odds_data=market_probs,
            )

        # ── Post-flight gate (P0-4) ──
        _run_postflight_gate(
            result,
            is_knockout=bool(stage and stage in (
                "Round of 32", "Round of 16", "Quarter-finals",
                "Semi-finals", "Final", "Third Place",
            )),
            market_required=enable_market,
        )

        return result

# ── Helpers ────────────────────────────────────────────────

def _lookup_wc_stage(home_team: str, away_team: str, *, match_id: str = "") -> str:
    """Compatibility lookup for callers that did not pass explicit stage.

    Match identity is preferred. A team-pair fallback is accepted only when it
    resolves to exactly one distinct stage, preventing an old fixture from
    silently supplying weights for a new match.
    """
    try:
        import sqlite3
        db_path = current_sync_sqlite_path()
        if not db_path.exists():
            return ""
        with sqlite3.connect(str(db_path)) as conn:
            if match_id:
                row = conn.execute(
                    "SELECT stage FROM wc26_schedule WHERE CAST(id AS TEXT)=?",
                    (str(match_id),),
                ).fetchone()
                if row and row[0]:
                    return str(row[0])
            rows = conn.execute(
                "SELECT DISTINCT stage FROM wc26_schedule "
                "WHERE home_team=? AND away_team=? AND stage IS NOT NULL",
                (home_team, away_team),
            ).fetchall()
        return str(rows[0][0]) if len(rows) == 1 and rows[0][0] else ""
    except Exception:
        return ""


def _default_competition_weight(competition: str) -> float:
    """Auto-detect competition_weight from competition name."""
    c = competition.lower()
    if "world cup" in c:
        return WORLD_CUP_COMPETITION_WEIGHT
    if any(kw in c for kw in ["friendly", "international friendly"]):
        return FRIENDLY_COMPETITION_WEIGHT
    return DEFAULT_COMPETITION_WEIGHT


def _run_async_in_thread(coro):
    """Run an async coroutine from sync code via a new event loop in a thread.

    Used by ``predict_sync()`` for best-effort async calls (market, etc.).
    Never raises — returns None on failure.
    """
    import asyncio
    import threading

    result_holder: list[Any] = []
    error_holder: list[Exception] = []

    def _runner() -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result_holder.append(loop.run_until_complete(coro))
            finally:
                loop.close()
        except Exception as exc:
            error_holder.append(exc)

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=15.0)
    if t.is_alive():
        logger.warning("_run_async_in_thread: coroutine timed out after 15s — "
                       "market data may be stale")
        return None
    if error_holder:
        logger.warning("_run_async_in_thread: coroutine raised %s — "
                       "market data unavailable", error_holder[0])
        return None
    return result_holder[0] if result_holder else None


def _initial_source_status(
    *,
    enable_weather: bool,
    enable_market: bool,
    require_full_context: bool,
) -> dict[str, SourceStatus]:
    """Initial real-time source status map for sync predictions."""
    return {
        "match_context": SourceStatus(
            status="skipped",
            reason="not_evaluated",
            attempted=False,
            required=require_full_context,
        ),
        "injuries": SourceStatus(
            status="skipped",
            reason="not_evaluated",
            attempted=False,
        ),
        "news": SourceStatus(
            status="skipped",
            reason="not_evaluated",
            attempted=False,
        ),
        "lineups": SourceStatus(
            status="skipped",
            reason="not_implemented_in_sync_pipeline",
            attempted=False,
        ),
        "weather": SourceStatus(
            status="skipped" if not enable_weather else "skipped",
            reason="disabled_by_flag" if not enable_weather else "not_evaluated",
            attempted=False,
            required=require_full_context,
        ),
        "market": SourceStatus(
            status="skipped" if not enable_market else "skipped",
            reason="disabled_by_flag" if not enable_market else "not_evaluated",
            attempted=False,
            required=require_full_context,
        ),
    }


def _match_context_status(
    *,
    match_id: str,
    match_date: str | datetime | None,
    venue: str | None,
    require_full_context: bool,
) -> SourceStatus:
    missing = []
    if not str(match_id or "").strip():
        missing.append("match_id")
    if match_date is None or not str(match_date).strip():
        missing.append("match_date")
    if not str(venue or "").strip():
        missing.append("venue")
    if missing:
        return SourceStatus(
            status="unavailable",
            reason="missing_context",
            detail=",".join(missing),
            attempted=True,
            required=require_full_context,
        )
    return SourceStatus(
        status="used",
        reason="explicit_context_supplied",
        attempted=True,
        required=require_full_context,
    )


def _validate_required_sync_context(
    *,
    match_id: str,
    match_date: str | datetime | None,
    venue: str | None,
    enable_weather: bool,
    enable_market: bool,
) -> None:
    """Fail before running strict sync prediction with insufficient context."""
    missing = []
    if not str(match_id or "").strip():
        missing.append("match_id")
    if match_date is None or not str(match_date).strip():
        missing.append("match_date")
    if not str(venue or "").strip():
        missing.append("venue")
    if not enable_weather:
        missing.append("enable_weather")
    if not enable_market:
        missing.append("enable_market")
    if missing:
        raise ValueError(
            "require_full_context=True requires explicit "
            + ", ".join(missing)
            + ". Use enhanced_best_effort/artifact_only when those inputs are unavailable."
        )


def _apply_required_source_gate(
    *,
    source_status: dict[str, SourceStatus],
    degraded_reasons: list[DegradedReason],
    quality: Any,
) -> None:
    """Mark strict sync predictions degraded when required sources did not resolve."""
    for source in ("match_context", "weather", "market"):
        status = source_status.get(source)
        if status is None or status.status == "used":
            continue
        degraded_reasons.append(DegradedReason(
            source=source,
            reason=status.reason or status.status,
            severity="error",
            detail=status.detail,
        ))
        if hasattr(quality, "mark_degraded"):
            quality.mark_degraded(
                f"Required source {source} is {status.status}: {status.reason}"
            )


def _coerce_match_datetime(value: str | datetime | None) -> datetime | None:
    """Convert optional user-supplied match date to ``datetime``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            logger.debug("Invalid match_date supplied to predict_sync: %r", value)
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _save_snapshot_sync(
    *,
    result: "PredictionResult",
    quality: Any,
    component_probs: dict,
    fg: Any,
    wc: Any,
    match_id: str = "",
    kickoff_at: str = "",
    injury_signals_count: int = 0,
    injury_data_available: bool = False,
    news_signals_count: int = 0,
    news_signals_available: bool = False,
    news_signal_ids: list[str] | None = None,
    weather_available: bool = False,
    weather_data: dict | None = None,
    odds_available: bool = False,
    odds_data: dict | None = None,
) -> None:
    """Save a PreMatchSnapshot from sync artifact prediction.

    Raises RuntimeError on save failure — silent snapshot loss prevents
    self-evolution and post-match analysis (P0-1 fix).
    """
    from app.services.snapshot_service import save_pre_match_snapshot
    from app.version import VERSION, get_git_commit

    risk_tags = list(result.risk_tags or [])
    if hasattr(quality, "warnings"):
        for w in quality.warnings:
            risk_tags.append(w)

    degraded: list[dict[str, str]] = []
    if hasattr(quality, "warnings"):
        for w in quality.warnings:
            degraded.append({"source": "pipeline", "reason": w, "severity": "warning"})

    snapshot_id = save_pre_match_snapshot(
        home_team=result.home_team,
        away_team=result.away_team,
        competition=result.competition,
        is_neutral=result.is_neutral,
        match_id=match_id or result.match_id,
        kickoff_at=kickoff_at or result.match_date,
        prediction_mode="full",
        final_home_prob=result.home_win_prob,
        final_draw_prob=result.draw_prob,
        final_away_prob=result.away_win_prob,
        home_xg=result.home_xg,
        away_xg=result.away_xg,
        top_scores=result.top_scores,
        fused_score_matrix=result.score_matrix if result.score_matrix else None,
        source_score_matrices=result.source_score_matrices if result.source_score_matrices else None,
        component_probs=component_probs,
        weight_config_label=getattr(wc, "label", ""),
        weight_config=wc.to_dict() if hasattr(wc, "to_dict") else None,
        effective_weights=fg.effective_weights if hasattr(fg, "effective_weights") else None,
        fusion_graph=fg.to_dict() if hasattr(fg, "to_dict") else {},
        model_disagreement=(
            fg.model_disagreement.get("max_home_diff", 0.0)
            if hasattr(fg, "model_disagreement") and fg.model_disagreement
            else 0.0
        ),
        market_blended=result.market_applied,
        market_weight_used=result.market_weight_used,
        market_divergence=result.divergence,
        confidence="medium",
        confidence_penalty=result.confidence_penalty,
        risk_tags=risk_tags,
        pipeline_status=getattr(quality, "pipeline_status", "unknown"),
        missing_inputs=result.missing_inputs,
        degraded_reasons=degraded,
        code_version=VERSION,
        git_commit=get_git_commit(),
        injury_data_available=injury_data_available,
        news_signals_available=news_signals_available,
        news_signal_ids=news_signal_ids or [],
        weather_available=weather_available,
        weather_snapshot=weather_data,
        odds_available=odds_available,
        odds_snapshot=odds_data,
    )

    if snapshot_id is None:
        raise RuntimeError(
            f"PreMatchSnapshot save FAILED for {result.home_team} vs {result.away_team}. "
            f"match_id={match_id or result.match_id}.  "
            f"Snapshot persistence is REQUIRED for post-match learning — "
            f"prediction is incomplete without it.  Check DB connectivity and "
            f"schema (run migration if needed)."
        )
