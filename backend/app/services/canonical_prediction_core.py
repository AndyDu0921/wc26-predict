"""Single in-process adapter for every production prediction surface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.services.prediction_pipeline import PredictionPipeline
from app.services.prediction_result import PredictionResult


@dataclass(frozen=True)
class PredictionInvocation:
    home_team: str
    away_team: str
    competition: str
    is_neutral: bool = False
    match_id: str = ""
    kickoff_at: str | datetime | None = None
    stage: str = ""
    venue: str | None = None
    mode: str = "full"
    save_snapshot: bool = True
    enable_market: bool = True
    enable_weather: bool = True
    require_full_context: bool = False


def execute_prediction_core(
    invocation: PredictionInvocation,
    *,
    pipeline: PredictionPipeline | None = None,
) -> PredictionResult:
    """Execute the only supported model inference chain.

    Persistence orchestration differs between the async API and the manual CLI,
    but both must enter model inference through this adapter. Keeping the
    invocation immutable also prevents a caller from changing stage or kickoff
    halfway through the run.
    """
    selected_pipeline = pipeline or PredictionPipeline.from_artifacts(mode=invocation.mode)
    return selected_pipeline.predict_sync(
        invocation.home_team,
        invocation.away_team,
        invocation.competition,
        is_neutral=invocation.is_neutral,
        mode=invocation.mode,
        match_id=invocation.match_id,
        match_date=invocation.kickoff_at,
        stage=invocation.stage,
        venue=invocation.venue,
        save_snapshot=invocation.save_snapshot,
        enable_market=invocation.enable_market,
        enable_weather=invocation.enable_weather,
        require_full_context=invocation.require_full_context,
    )
