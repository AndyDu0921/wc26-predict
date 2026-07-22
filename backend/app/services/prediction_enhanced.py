"""prediction_enhanced.py — Dashboard/Creator compatibility orchestrator.

Wraps PredictionPipeline with optional presentation-layer enrichment:
- Market odds telemetry through the pipeline shadow-mode path
- Weather (Open-Meteo, free)
- DeepSeek V4 Pro AI analysis

Design principles:
1. PredictionPipeline owns artifact inference and market policy
2. Every real-time data source has graceful degradation
3. Weather and LLM enrichment stay optional and non-blocking
4. All functions are synchronous (for Streamlit compatibility)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Legacy Dashboard argument retained for API compatibility. The canonical
# pipeline owns the actual market blend policy.
MAX_MARKET_BLEND = 0.25
DIVERGENCE_THRESHOLD = 0.12


@dataclass
class EnhancedPredictionResult:
    """Complete enhanced prediction output.

    Contains base artifact prediction + optional market/weather/LLM data.
    """

    # ── Match info ──
    home_team: str
    away_team: str
    competition: str
    is_neutral: bool
    mode: str

    # ── Base artifact prediction ──
    base_result: dict[str, Any] = field(default_factory=dict)
    base_quality: Any = None  # RunQuality
    timings: Any = None  # PredictionTimer

    # ── Final probabilities (after market blend, if applied) ──
    final_home_prob: float | None = None
    final_draw_prob: float | None = None
    final_away_prob: float | None = None

    # ── Market data ──
    market_probs: dict[str, Any] | None = None
    market_blended: bool = False
    market_weight_used: float = 0.0
    market_divergence: float | None = None
    market_divergence_triggered: bool = False
    source_status: dict[str, Any] = field(default_factory=dict)

    # ── Weather ──
    weather: dict[str, Any] | None = None
    weather_impact_tags: list[str] = field(default_factory=list)

    # ── LLM analysis ──
    llm_analysis: str | None = None
    llm_video_script: str | None = None
    llm_social_copy: str | None = None
    llm_error: str | None = None

    # ── Metadata ──
    enhanced_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    components_used: list[str] = field(default_factory=list)
    risk_tags: list[str] = field(default_factory=list)
    confidence_penalty: float = 0.0
    total_seconds: float = 0.0

    @property
    def is_enhanced(self) -> bool:
        """True if any real-time data was successfully incorporated."""
        return self.market_probs is not None or self.weather is not None

    @property
    def is_ai_analyzed(self) -> bool:
        """True if LLM analysis was generated."""
        return self.llm_analysis is not None


# ── Main entry point ────────────────────────────────────────────────────────────


def run_enhanced_prediction(
    home_team: str,
    away_team: str,
    competition: str = "International Friendly",
    is_neutral: bool = True,
    mode: str = "full",
    *,
    enable_market: bool = True,
    enable_weather: bool = True,
    enable_llm: bool = True,
    market_blend_max: float = MAX_MARKET_BLEND,
    match_id: str | None = None,
    match_date: str | datetime | None = None,
    stage: str = "",
    venue: str | None = None,
    save_snapshot: bool = False,
    require_full_context: bool = False,
) -> EnhancedPredictionResult:
    """Run Dashboard/Creator prediction with optional enrichment.

    Flow:
    1. PredictionPipeline artifact prediction
    2. Market odds telemetry through pipeline shadow-mode (optional)
    3. Weather fetch (optional)
    4. LLM analysis generation (optional)

    All optional steps degrade gracefully — failure in any step
    does not prevent the overall prediction from completing.

    Args:
        home_team: Home team name.
        away_team: Away team name.
        competition: Competition name.
        is_neutral: Neutral venue flag.
        mode: Prediction mode (baseline/standard/full/research-full).
        enable_market: Fetch market odds through PredictionPipeline shadow-mode.
        enable_weather: Fetch weather data.
        enable_llm: Generate AI analysis via DeepSeek.
        market_blend_max: Legacy compatibility parameter; market policy is
            owned by PredictionPipeline.
        match_id: Optional real match identifier for closed-loop traceability.
        match_date: Optional kickoff timestamp for weather/leakage metadata.
        venue: Optional venue name.
        save_snapshot: Persist a prediction snapshot through PredictionPipeline.
        require_full_context: Enforce explicit match context and required live sources.

    Returns:
        EnhancedPredictionResult with all available data.
    """
    import time as time_module

    _ = market_blend_max
    t_start = time_module.perf_counter()
    result = EnhancedPredictionResult(
        home_team=home_team,
        away_team=away_team,
        competition=competition,
        is_neutral=is_neutral,
        mode=mode,
    )

    try:
        from app.services.canonical_prediction_core import (
            PredictionInvocation,
            execute_prediction_core,
        )
        from app.services.run_quality import RunQuality

        pred = execute_prediction_core(PredictionInvocation(
            home_team=home_team,
            away_team=away_team,
            competition=competition,
            is_neutral=is_neutral,
            mode=mode,
            match_id=match_id or "",
            kickoff_at=match_date,
            stage=stage,
            venue=venue,
            save_snapshot=save_snapshot,
            enable_market=enable_market,
            enable_weather=enable_weather if require_full_context else False,
            require_full_context=require_full_context,
        ))

        result.base_result = _prediction_result_to_flat_dict(pred)
        result.final_home_prob = pred.home_win_prob
        result.final_draw_prob = pred.draw_prob
        result.final_away_prob = pred.away_win_prob
        result.market_probs = pred.market_probs if enable_market else None
        result.market_blended = pred.market_applied
        result.market_weight_used = pred.market_weight_used
        result.market_divergence = pred.divergence
        result.market_divergence_triggered = pred.divergence > DIVERGENCE_THRESHOLD
        result.source_status = {
            key: value.to_dict() if hasattr(value, "to_dict") else value
            for key, value in pred.source_status.items()
        }
        result.components_used = list(pred.components_used)
        result.risk_tags = list(pred.risk_tags)
        result.confidence_penalty = pred.confidence_penalty

        quality = RunQuality()
        quality.pipeline_status = "full"
        for component in pred.components_used:
            quality.model_components[component] = "loaded_from_artifact"
        for degraded in pred.degraded_reasons:
            quality.mark_degraded(f"{degraded.source}: {degraded.reason}")
        result.base_quality = quality

    except Exception as exc:
        logger.error("Base enhanced prediction failed: %s", exc)
        raise RuntimeError(f"Base enhanced prediction failed: {exc}") from exc

    if enable_weather:
        weather = _fetch_weather(home_team, away_team, venue=venue)
        if weather and weather.get("forecast_available"):
            result.weather = weather
            try:
                from app.services.weather_service import WeatherService

                result.weather_impact_tags = WeatherService().weather_impact_tags(weather)
                result.risk_tags.extend(result.weather_impact_tags)
            except Exception as exc:
                logger.warning("Weather impact tagging failed: %s", exc)

    if enable_llm:
        try:
            llm_result = _generate_llm_analysis(result)
            if llm_result:
                if llm_result.get("degraded"):
                    result.llm_error = str(llm_result.get("reason", "llm_degraded"))
                else:
                    result.llm_analysis = llm_result.get("analysis")
                    result.llm_video_script = llm_result.get("video_script")
                    result.llm_social_copy = llm_result.get("social_copy")
        except Exception as exc:
            logger.warning("LLM analysis failed: %s", exc)
            result.llm_error = str(exc)

    result.total_seconds = time_module.perf_counter() - t_start
    return result


# ── Internal helpers ────────────────────────────────────────────────────────────


def _prediction_result_to_flat_dict(pred: Any) -> dict[str, Any]:
    """Flatten PredictionResult into the legacy Dashboard result shape."""
    data = pred.to_dict()
    return {
        "home_team": pred.home_team,
        "away_team": pred.away_team,
        "competition": pred.competition,
        "is_neutral": pred.is_neutral,
        "match_id": pred.match_id,
        "match_date": pred.match_date,
        "mode": pred.mode,
        "home_win_prob": pred.home_win_prob,
        "draw_prob": pred.draw_prob,
        "away_win_prob": pred.away_win_prob,
        "home_xg": pred.home_xg,
        "away_xg": pred.away_xg,
        "top_scores": pred.top_scores,
        "components_used": list(pred.components_used),
        "risk_tags": list(pred.risk_tags),
        "fusion_graph": data.get("fusion_graph", {}),
        "component_probs": data.get("component_probs", {}),
        "source_status": data.get("source_status", {}),
        "degraded_reasons": data.get("degraded_reasons", []),
        "missing_inputs": data.get("missing_inputs", []),
    }


def _fetch_weather(
    home_team: str,
    away_team: str,
    *,
    venue: str | None = None,
) -> dict[str, Any] | None:
    """Fetch weather synchronously. Returns None on failure."""
    try:
        from app.services.weather_service import WeatherService

        ws = WeatherService()
        return ws.get_weather_for_match_sync(
            venue=venue,
            home_team=home_team,
            away_team=away_team,
        )
    except ImportError as e:
        logger.warning("WeatherService not available: %s", e)
        return None
    except Exception as e:
        logger.warning("Weather fetch error: %s", e)
        return None


def _generate_llm_analysis(
    result: EnhancedPredictionResult,
) -> dict[str, str] | None:
    """Generate AI analysis via DeepSeek V4 Pro.

    Uses asyncio.run() to call the async DeepSeek client.
    Generates three content types: analysis article, video script, social copy.

    Returns dict with 'analysis', 'video_script', 'social_copy' keys,
    or None if all generations failed.  Returns degraded flag if called
    from within an existing event loop (e.g., FastAPI async route).
    """
    import asyncio
    import logging

    _log = logging.getLogger(__name__)

    # Check for existing event loop before calling asyncio.run()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        _log.warning(
            "LLM analysis skipped — asyncio.run() cannot be called from "
            "existing event loop. Returning degraded result."
        )
        return {
            "degraded": True,
            "source": "llm_analysis",
            "reason": "event_loop_conflict",
        }

    return asyncio.run(_generate_llm_analysis_async(result))


async def _generate_llm_analysis_async(
    result: EnhancedPredictionResult,
) -> dict[str, str] | None:
    """Async implementation of LLM analysis generation."""
    try:
        from app.services.llm.analysis_prompts import (
            MATCH_ANALYSIS_SYSTEM,
            MATCH_ANALYSIS_USER,
            VIDEO_SCRIPT_SYSTEM,
            VIDEO_SCRIPT_USER,
            SOCIAL_COPY_SYSTEM,
            SOCIAL_COPY_USER,
            build_market_section,
            build_market_summary,
            build_source_status_section,
            build_weather_section,
            build_team_context,
            build_key_points,
        )
        from app.services.llm.deepseek_client import DeepSeekClient
    except ImportError as e:
        logger.warning("LLM modules not available: %s", e)
        return None

    client = DeepSeekClient()

    base = result.base_result
    h = result.final_home_prob
    d = result.final_draw_prob
    a = result.final_away_prob
    home = result.home_team
    away = result.away_team
    comp = result.competition

    # Format top scores
    scores = base.get("top_scores", [])
    top_scores_str = "、".join(
        f"{s['score']} ({s['prob']:.1%})" for s in scores[:3]
    ) if scores else "暂无"

    # Build disagreement string
    fg = base.get("fusion_graph", {})
    disagreement = fg.get("model_disagreement", {})
    max_diff = disagreement.get("max_home_diff", 0)
    if max_diff > 0.3:
        disagreement_str = f"高 ({max_diff:.1%}) — 模型间存在显著方向性分歧"
    elif max_diff > 0.15:
        disagreement_str = f"中等 ({max_diff:.1%})"
    else:
        disagreement_str = f"低 ({max_diff:.1%}) — 模型方向一致"

    output: dict[str, str] = {}

    # ── 1. Match analysis article ──
    try:
        market_section = build_market_section(result.market_probs)
        weather_section = build_weather_section(result.weather)
        source_status_section = build_source_status_section(result.source_status)
        team_context = build_team_context(home, away)
        user_prompt = MATCH_ANALYSIS_USER.format(
            home_team=home,
            away_team=away,
            competition=comp,
            venue="中立场地" if result.is_neutral else f"{home} 主场",
            match_time="即将进行",
            home_prob=h,
            draw_prob=d,
            away_prob=a,
            home_xg=base.get("home_xg", 0),
            away_xg=base.get("away_xg", 0),
            top_scores=top_scores_str,
            disagreement=disagreement_str,
            market_section=market_section,
            weather_section=weather_section,
            source_status_section=source_status_section,
            team_context=team_context,
        )
        analysis = await client.chat(
            system=MATCH_ANALYSIS_SYSTEM,
            user=user_prompt,
            response_format="text",
        )
        if analysis:
            output["analysis"] = analysis
    except Exception as e:
        logger.warning(f"LLM analysis article generation failed: {e}")

    # ── 2. Video script ──
    try:
        key_points = build_key_points(base, result.market_probs)
        market_summary = build_market_summary(result.market_probs)
        source_status_section = build_source_status_section(result.source_status)
        script_prompt = VIDEO_SCRIPT_USER.format(
            home_team=home,
            away_team=away,
            competition=comp,
            venue="中立场地" if result.is_neutral else f"{home} 主场",
            home_prob=h,
            draw_prob=d,
            away_prob=a,
            home_xg=base.get("home_xg", 0),
            away_xg=base.get("away_xg", 0),
            top_scores=top_scores_str,
            market_summary=market_summary,
            key_points=key_points,
            source_status_section=source_status_section,
        )
        script = await client.chat(
            system=VIDEO_SCRIPT_SYSTEM,
            user=script_prompt,
            response_format="text",
        )
        if script:
            output["video_script"] = script
    except Exception as e:
        logger.warning(f"LLM video script generation failed: {e}")

    # ── 3. Social copy ──
    try:
        social_prompt = SOCIAL_COPY_USER.format(
            home_team=home,
            away_team=away,
            competition=comp,
            home_prob=h,
            draw_prob=d,
            away_prob=a,
            home_xg=base.get("home_xg", 0),
            away_xg=base.get("away_xg", 0),
        )
        social = await client.chat(
            system=SOCIAL_COPY_SYSTEM,
            user=social_prompt,
            response_format="text",
        )
        if social:
            output["social_copy"] = social
    except Exception as e:
        logger.warning(f"LLM social copy generation failed: {e}")

    return output if output else None


# ── Convenience: convert to Dashboard-compatible dict ───────────────────────────


def enhanced_result_to_dict(result: EnhancedPredictionResult) -> dict[str, Any]:
    """Convert EnhancedPredictionResult to a dict for st.session_state storage."""
    base = result.base_result
    return {
        "home_team": result.home_team,
        "away_team": result.away_team,
        "competition": result.competition,
        "is_neutral": result.is_neutral,
        "mode": result.mode,
        # Final probabilities (after market blend if applicable)
        "home_win_prob": result.final_home_prob,
        "draw_prob": result.final_draw_prob,
        "away_win_prob": result.final_away_prob,
        # From base
        "home_xg": base.get("home_xg", 0),
        "away_xg": base.get("away_xg", 0),
        "top_scores": base.get("top_scores", []),
        "components_used": result.components_used,
        "fusion_graph": base.get("fusion_graph", {}),
        # Enhanced data
        "market_probs": result.market_probs,
        "market_blended": result.market_blended,
        "market_divergence": result.market_divergence,
        "market_divergence_triggered": result.market_divergence_triggered,
        "source_status": result.source_status,
        "weather": result.weather,
        "weather_impact_tags": result.weather_impact_tags,
        # LLM content
        "llm_analysis": result.llm_analysis,
        "llm_video_script": result.llm_video_script,
        "llm_social_copy": result.llm_social_copy,
        # Metadata
        "risk_tags": result.risk_tags,
        "confidence_penalty": result.confidence_penalty,
        "total_seconds": result.total_seconds,
        "is_enhanced": result.is_enhanced,
        "is_ai_analyzed": result.is_ai_analyzed,
        "enhanced_at": result.enhanced_at,
    }
