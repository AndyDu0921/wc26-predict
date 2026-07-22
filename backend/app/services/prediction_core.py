"""prediction_core.py — shared model-loading helpers for the prediction pipeline.

The current entry point is PredictionPipeline.from_artifacts(...).predict_sync(...)
or scripts/predict_match_full.py for the full pipeline (DC → Enhancer → Elo → Pi → Market).

Provides:
    _load_dc, _load_enhancer, _load_elo, _load_pi, _load_training_df
        → model objects ready for prediction
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from app.services.prediction_timer import PredictionTimer
from app.services.dixon_coles import DixonColesModel
from app.services.tabular_match_model import TabularMatchEnhancer
from app.services.weibull_model import WeibullWrapper
from app.services.elo_ratings import EloRatingSystem
from app.services.pi_ratings import PiRatingWrapper
from app.services.artifact_bundle import load_verified_pickle, verified_artifact_path
from app.services.sqlite_paths import current_sync_sqlite_path

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]

# Components that each mode expects in the registry
MODE_REQUIRED_COMPONENTS: dict[str, list[str]] = {
    "baseline": ["dixon_coles"],
    "standard": ["dixon_coles", "tabular_enhancer", "elo"],
    "full": ["dixon_coles", "tabular_enhancer", "elo", "pi_rating"],
    "research-full": ["dixon_coles", "tabular_enhancer", "elo", "pi_rating"],
}

MODE_LABELS: dict[str, str] = {
    "baseline": "baseline",
    "standard": "standard",
    "full": "full",
    "research-full": "research-full",
}


# ── Artifact loaders ─────────────────────────────────────────────────────────


def _load_dc(timer: PredictionTimer) -> DixonColesModel:
    """Load the exact hash-verified Dixon-Coles artifact or fail closed."""
    timer.start("load_dc")
    dc = _load_registered_model(
        prefix="dc",
        model_class=DixonColesModel,
    )
    timer.stop()
    return dc


def _load_enhancer(timer: PredictionTimer) -> TabularMatchEnhancer:
    """Load the exact hash-verified enhancer artifact or fail closed."""
    timer.start("load_enhancer")

    import warnings as _warnings

    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore", category=UserWarning)
        enhancer = _load_registered_model(
            prefix="enhancer",
            model_class=TabularMatchEnhancer,
        )
    timer.stop()
    return enhancer


def _load_registered_model(
    *,
    prefix: str,
    model_class: type,
) -> Any:
    """Load the exact hash-verified cache registered in the active bundle.

    Missing, tampered, or incompatible required artifacts are fatal. Prediction
    must never train a replacement model implicitly because that would make a
    single model version refer to multiple parameter states.
    """
    component_name = "dixon_coles" if prefix == "dc" else "tabular_enhancer"
    latest = verified_artifact_path(component_name)
    try:
        cached = load_verified_pickle(component_name)
    except Exception as exc:
        logger.error("Disk cache %s load failed: %s", latest.name, exc)
        raise

    from app.services.model_cache import CachedDC, CachedEnhancer

    # If it's already the right type, return directly
    if isinstance(cached, model_class):
        logger.info("%s loaded from disk cache: %s", prefix.upper(), latest.name)
        return cached

    # If it's a CachedDC / CachedEnhancer wrapper, reconstruct
    if isinstance(cached, CachedDC) and model_class is DixonColesModel:
        model = DixonColesModel()
        model.attack_params = cached.attack_params
        model.defense_params = cached.defense_params
        model.home_advantage = cached.home_advantage
        model.rho = cached.rho
        model._team_order = cached._team_order
        model.trained_at = cached.trained_at
        logger.info("DC reconstructed from disk cache: %s", latest.name)
        return model
    if isinstance(cached, CachedEnhancer) and model_class is TabularMatchEnhancer:
        model = TabularMatchEnhancer()
        model.model = cached.model
        model.feature_columns = cached.feature_columns
        model.is_fitted = True
        model.training_sample_count = cached.training_sample_count
        model.fitted_at = cached.fitted_at
        logger.info("Enhancer reconstructed from disk cache: %s", latest.name)
        return model

    raise TypeError(f"Unexpected {prefix} cache type {type(cached).__name__} in {latest.name}")


def _load_elo(timer: PredictionTimer) -> EloRatingSystem:
    """Load Elo ratings from JSON artifact and restore EloRatingSystem."""
    timer.start("load_elo")
    elo_path = verified_artifact_path("elo")
    elo_data = json.loads(elo_path.read_text("utf-8"))
    elo = EloRatingSystem()
    elo.ratings = {str(k): float(v) for k, v in elo_data.items()}
    timer.stop()
    return elo


def _load_pi(timer: PredictionTimer) -> PiRatingWrapper:
    """Load Pi-Ratings from JSON artifact and restore PiRatingWrapper."""
    timer.start("load_pi")
    pi_path = verified_artifact_path("pi_rating")
    pi_data = json.loads(pi_path.read_text("utf-8"))
    pi_model = PiRatingWrapper()
    pi_model.team_ratings = {str(k): float(v) for k, v in pi_data.items()}
    timer.stop()
    return pi_model


def _try_load_weibull(timer: PredictionTimer) -> WeibullWrapper | None:
    """Attempt to load a pre-fitted Weibull model from pickle.

    Weibull is not part of the standard artifact bundle — returns None
    if the file does not exist.
    """
    try:
        verified_artifact_path("weibull")
    except (FileNotFoundError, KeyError):
        return None
    timer.start("load_weibull")
    try:
        wb = load_verified_pickle("weibull")
        if isinstance(wb, WeibullWrapper) and wb._fitted:
            logger.info("  [load] Weibull model loaded from artifact")
            timer.stop()
            return wb
    except Exception as exc:
        logger.warning(f"  [load] Weibull load failed: {exc}")
    timer.stop()
    return None


def _load_training_df(timer: PredictionTimer) -> pd.DataFrame:
    """Load current national-team feature history from the canonical SQLite DB."""
    timer.start("load_df")
    db_path = current_sync_sqlite_path()
    if not db_path.exists():
        raise FileNotFoundError(f"Canonical feature-history database not found: {db_path}")
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    df = pd.read_sql_query(
        """
        SELECT ht.name AS home_team, at.name AS away_team,
               mr.home_goals, mr.away_goals, m.match_date,
               COALESCE(m.competition_weight, 1.0) AS competition_weight,
               COALESCE(m.is_neutral_venue, 0) AS is_neutral_venue,
               m.competition, m.stage
        FROM matches m
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
        JOIN match_results mr ON m.id = mr.match_id
        WHERE m.status = 'finished'
          AND ht.team_type = 'national'
          AND at.team_type = 'national'
        ORDER BY m.match_date ASC
    """,
        conn,
    )
    conn.close()
    df["match_date"] = pd.to_datetime(df["match_date"], utc=True, format="ISO8601")
    logger.info(
        f"  [data] Training DF: {len(df)} rows, {df.home_team.nunique()} teams (SQLite)",
    )
    timer.stop()
    return df
