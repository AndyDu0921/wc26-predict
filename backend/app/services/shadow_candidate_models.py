"""Shadow-only candidate probability models for V4.9 experiments.

These candidates are deliberately lightweight and auditable.  They are not
production models and do not mutate artifacts or weights.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.services.dixon_coles import DixonColesModel
from app.services.player_availability import build_player_availability_shadow


SUPPORTED_SHADOW_CANDIDATES = {
    "uniform_baseline",
    "current_fusion",
    "dynamic_dixon_coles",
    "dynamic_bivariate_poisson",
    "bayesian_weighted_dynamic",
    "dynamic_bayesian_weighted_goal_model",
    "covariate_ml_baseline",
    "international_covariate_hybrid",
    "dirichlet_calibration",
    "dirichlet_calibration_candidate",
    "stacking_optimizer",
    "proper_scoring_stacking_candidate",
    "player_availability_shadow",
}

CANDIDATE_ALIASES = {
    "dynamic_bayesian_weighted_goal_model": "bayesian_weighted_dynamic",
    "international_covariate_hybrid": "covariate_ml_baseline",
    "dirichlet_calibration_candidate": "dirichlet_calibration",
    "proper_scoring_stacking_candidate": "stacking_optimizer",
}

CANDIDATE_FAMILIES = {
    "uniform_baseline": "baseline",
    "current_fusion": "champion",
    "dynamic_dixon_coles": "dynamic_goal_model",
    "dynamic_bivariate_poisson": "dynamic_goal_model",
    "bayesian_weighted_dynamic": "dynamic_goal_model",
    "dynamic_bayesian_weighted_goal_model": "dynamic_goal_model",
    "covariate_ml_baseline": "covariate_hybrid",
    "international_covariate_hybrid": "covariate_hybrid",
    "dirichlet_calibration": "calibrator",
    "dirichlet_calibration_candidate": "calibrator",
    "stacking_optimizer": "stacking",
    "proper_scoring_stacking_candidate": "stacking",
    "player_availability_shadow": "player_availability",
}

DYNAMIC_DC_HALF_LIFE_DAYS = 180
DYNAMIC_DC_MAX_HISTORY_DAYS = 4 * 365


@dataclass(frozen=True)
class ShadowCandidateResult:
    candidate_name: str
    available: bool
    probs: dict[str, float] | None = None
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    score_matrix: list[list[float]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HistoricalMatch:
    home_team: str
    away_team: str
    match_date: datetime
    home_goals: int
    away_goals: int
    stage: str
    is_neutral: bool


def build_shadow_candidate_prediction(
    candidate_name: str,
    row: dict[str, Any],
    *,
    db_path: str | Path,
    registry_rows: list[dict[str, Any]] | None = None,
) -> ShadowCandidateResult:
    """Build a shadow candidate probability distribution for one sample."""
    if candidate_name not in SUPPORTED_SHADOW_CANDIDATES:
        return ShadowCandidateResult(candidate_name, False, reason="unsupported_candidate")
    canonical_name = CANDIDATE_ALIASES.get(candidate_name, candidate_name)
    if canonical_name == "uniform_baseline":
        return ShadowCandidateResult(
            candidate_name,
            True,
            probs={"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3},
            reason="uniform_reference",
            payload={"candidate_family": candidate_family(candidate_name), "shadow_only": True},
        )
    if canonical_name == "current_fusion":
        current = row.get("current_probs")
        if not isinstance(current, dict):
            return ShadowCandidateResult(candidate_name, False, reason="missing_current_probs")
        return ShadowCandidateResult(
            candidate_name,
            True,
            probs=_normalize(current),
            reason="identity_champion",
            payload={"candidate_family": candidate_family(candidate_name), "shadow_only": True},
        )

    if canonical_name == "player_availability_shadow":
        return _player_availability_shadow(candidate_name, row, db_path=db_path)

    kickoff = _parse_dt(row.get("kickoff_at") or row.get("match_date"))
    if kickoff is None:
        return ShadowCandidateResult(candidate_name, False, reason="kickoff_time_unavailable")

    if canonical_name in {
        "dynamic_dixon_coles",
        "dynamic_bivariate_poisson",
        "bayesian_weighted_dynamic",
    }:
        participant_pool: set[str] | None = None
        max_age_days: int | None = None
        if canonical_name == "dynamic_dixon_coles":
            participant_pool = _load_world_cup_participant_pool(db_path)
            max_age_days = DYNAMIC_DC_MAX_HISTORY_DAYS
        history = _load_history(
            db_path,
            before=kickoff,
            team_pool=participant_pool,
            max_age_days=max_age_days,
        )
        if len(history) < 100:
            return ShadowCandidateResult(
                candidate_name,
                False,
                reason=f"insufficient_history_{len(history)}",
                payload={
                    "history_count": len(history),
                    "candidate_family": candidate_family(candidate_name),
                    "shadow_only": True,
                },
            )
        if canonical_name == "dynamic_dixon_coles":
            return _dynamic_dixon_coles_candidate(
                candidate_name,
                history,
                home_team=str(row["home_team"]),
                away_team=str(row["away_team"]),
                is_neutral=bool(row.get("is_neutral", True)),
                as_of=kickoff,
                participant_pool_size=len(participant_pool or ()),
                max_history_days=max_age_days,
            )
        lambdas = _dynamic_lambdas(
            history,
            home_team=str(row["home_team"]),
            away_team=str(row["away_team"]),
            as_of=kickoff,
            bayesian=canonical_name == "bayesian_weighted_dynamic",
        )
        if canonical_name == "dynamic_bivariate_poisson":
            score_matrix = _bivariate_poisson_matrix(
                lambdas["home_xg"],
                lambdas["away_xg"],
                shared_lambda=lambdas["shared_lambda"],
            )
        else:
            score_matrix = _independent_poisson_matrix(lambdas["home_xg"], lambdas["away_xg"])
        probs = _matrix_to_probs(score_matrix)
        lambdas["candidate_family"] = candidate_family(candidate_name)
        lambdas["canonical_candidate_name"] = canonical_name
        lambdas["shadow_only"] = True
        return ShadowCandidateResult(
            candidate_name,
            True,
            probs=probs,
            reason="computed_from_pre_match_history",
            payload=lambdas,
            score_matrix=score_matrix,
        )

    if canonical_name == "dirichlet_calibration":
        return _dirichlet_like_calibration(candidate_name, row, registry_rows or [])

    if canonical_name == "stacking_optimizer":
        return _stacking_optimizer(candidate_name, row, registry_rows or [])

    if canonical_name == "covariate_ml_baseline":
        return _covariate_hybrid_candidate(candidate_name, row, db_path=db_path, registry_rows=registry_rows or [])

    return ShadowCandidateResult(candidate_name, False, reason="not_implemented")


def _dynamic_dixon_coles_candidate(
    candidate_name: str,
    history: list[HistoricalMatch],
    *,
    home_team: str,
    away_team: str,
    is_neutral: bool,
    as_of: datetime,
    participant_pool_size: int,
    max_history_days: int | None,
) -> ShadowCandidateResult:
    """Fit a genuine expanding-window Dixon-Coles model for one cutoff."""
    frame = pd.DataFrame([
        {
            "home_team": match.home_team,
            "away_team": match.away_team,
            "match_date": match.match_date,
            "home_goals": match.home_goals,
            "away_goals": match.away_goals,
            "competition_weight": 1.0,
            "is_neutral_venue": match.is_neutral,
        }
        for match in history
    ])
    try:
        model = DixonColesModel(half_life_days=DYNAMIC_DC_HALF_LIFE_DAYS)
        fit = model.fit(frame)
        prediction = model.predict_match(
            home_team,
            away_team,
            is_neutral_venue=is_neutral,
        )
        score_matrix, _ = model.predict_score_matrix(
            home_team,
            away_team,
            is_neutral_venue=is_neutral,
            max_goals=10,
        )
    except Exception as exc:
        return ShadowCandidateResult(
            candidate_name,
            False,
            reason=f"dixon_coles_fit_failed:{type(exc).__name__}",
            payload={
                "history_count": len(history),
                "as_of": as_of.isoformat(),
                "shadow_only": True,
            },
        )
    return ShadowCandidateResult(
        candidate_name,
        True,
        probs=_normalize(prediction),
        reason="expanding_window_dixon_coles",
        payload={
            "model_kind": "dixon_coles_low_score_correlation",
            "history_count": len(history),
            "as_of": as_of.isoformat(),
            "half_life_days": model.half_life_days,
            "max_history_days": max_history_days,
            "training_scope": (
                "world_cup_participant_pool"
                if participant_pool_size
                else "all_national_teams_fallback"
            ),
            "training_team_pool_size": participant_pool_size or None,
            "discarded_match_max_weight_bound": (
                round(0.5 ** (max_history_days / model.half_life_days), 8)
                if max_history_days
                else None
            ),
            "rho": round(float(model.rho), 8),
            "fit_converged": bool(fit.converged),
            "fit_message": str(fit.message),
            "candidate_family": candidate_family(candidate_name),
            "shadow_only": True,
        },
        score_matrix=score_matrix.tolist(),
    )


def candidate_family(candidate_name: str) -> str:
    return CANDIDATE_FAMILIES.get(candidate_name, "unknown")


def _player_availability_shadow(
    candidate_name: str,
    row: dict[str, Any],
    *,
    db_path: str | Path,
) -> ShadowCandidateResult:
    current = row.get("current_probs")
    if not isinstance(current, dict):
        return ShadowCandidateResult(candidate_name, False, reason="missing_current_probs")
    probs = _normalize(current)
    snapshot = build_player_availability_shadow(
        str(row["home_team"]),
        str(row["away_team"]),
        db_path=db_path,
        as_of_time=str(row.get("as_of_time") or row.get("kickoff_at") or row.get("match_date") or ""),
    )
    payload = snapshot.to_dict()
    if not snapshot.adjustments:
        payload["probability_adjustment"] = {"home": 0.0, "draw": 0.0, "away": 0.0}
        payload["candidate_family"] = candidate_family(candidate_name)
        payload["shadow_only"] = True
        return ShadowCandidateResult(
            candidate_name,
            True,
            probs=probs,
            reason="no_player_availability_effect",
            payload=payload,
        )

    xg_delta = float(snapshot.home_xg_modifier) - float(snapshot.away_xg_modifier)
    tilt = _clamp(xg_delta * 0.18, -0.08, 0.08)
    adjusted = {
        "home": max(probs["home"] + tilt, 1e-6),
        "draw": max(probs["draw"] - abs(tilt) * 0.20, 1e-6),
        "away": max(probs["away"] - tilt, 1e-6),
    }
    payload["xg_delta_home_minus_away"] = round(xg_delta, 6)
    payload["candidate_family"] = candidate_family(candidate_name)
    payload["shadow_only"] = True
    payload["probability_adjustment"] = {
        "home": round(adjusted["home"] - probs["home"], 6),
        "draw": round(adjusted["draw"] - probs["draw"], 6),
        "away": round(adjusted["away"] - probs["away"], 6),
    }
    return ShadowCandidateResult(
        candidate_name,
        True,
        probs=_normalize(adjusted),
        reason="shadow_player_availability_adjustment",
        payload=payload,
    )


def _load_history(
    db_path: str | Path,
    *,
    before: datetime,
    team_pool: set[str] | None = None,
    max_age_days: int | None = None,
) -> list[HistoricalMatch]:
    path = Path(db_path)
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        match_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(matches)").fetchall()
        }
        team_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(teams)").fetchall()
        }
        scope_filters: list[str] = []
        if "competition_type" in match_columns:
            scope_filters.append("m.competition_type = 'national'")
        if "team_type" in team_columns:
            scope_filters.extend(("ht.team_type = 'national'", "at.team_type = 'national'"))
        params: list[Any] = []
        if team_pool:
            placeholders = ",".join("?" for _ in team_pool)
            scope_filters.extend(
                (
                    f"ht.name IN ({placeholders})",
                    f"at.name IN ({placeholders})",
                )
            )
            ordered_pool = sorted(team_pool)
            params.extend(ordered_pool)
            params.extend(ordered_pool)
        scope_sql = "" if not scope_filters else " AND " + " AND ".join(scope_filters)
        rows = conn.execute(
            f"""
            SELECT
                ht.name AS home_team,
                at.name AS away_team,
                m.match_date AS match_date,
                COALESCE(m.stage, '') AS stage,
                COALESCE(m.is_neutral_venue, 1) AS is_neutral_venue,
                mr.home_goals AS home_goals,
                mr.away_goals AS away_goals
            FROM matches m
            JOIN teams ht ON m.home_team_id = ht.id
            JOIN teams at ON m.away_team_id = at.id
            JOIN match_results mr ON m.id = mr.match_id
            WHERE mr.home_goals IS NOT NULL
              AND mr.away_goals IS NOT NULL
              {scope_sql}
            """,
            params,
        ).fetchall()
    finally:
        conn.close()
    history = []
    oldest_allowed = (
        before.timestamp() - max_age_days * 86400
        if max_age_days is not None
        else None
    )
    for item in rows:
        match_dt = _parse_dt(item["match_date"])
        if match_dt is None or match_dt >= before:
            continue
        if oldest_allowed is not None and match_dt.timestamp() < oldest_allowed:
            continue
        history.append(
            HistoricalMatch(
                home_team=str(item["home_team"]),
                away_team=str(item["away_team"]),
                match_date=match_dt,
                home_goals=int(item["home_goals"]),
                away_goals=int(item["away_goals"]),
                stage=str(item["stage"] or ""),
                is_neutral=bool(item["is_neutral_venue"]),
            )
        )
    return history


def _load_world_cup_participant_pool(db_path: str | Path) -> set[str] | None:
    """Load pre-tournament participant identities without reading results."""
    path = Path(db_path)
    if not path.exists():
        return None
    conn = sqlite3.connect(str(path))
    try:
        if not any(
            row[0] == "wc26_schedule"
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ):
            return None
        rows = conn.execute(
            """
            SELECT home_team, away_team
            FROM wc26_schedule
            WHERE stage = 'Group Stage'
            """
        ).fetchall()
    finally:
        conn.close()
    teams = {
        str(team).strip()
        for row in rows
        for team in row
        if team is not None and str(team).strip()
    }
    return teams or None


def _dynamic_lambdas(
    history: list[HistoricalMatch],
    *,
    home_team: str,
    away_team: str,
    as_of: datetime,
    bayesian: bool,
) -> dict[str, Any]:
    half_life_days = 180.0
    weighted = []
    for match in history:
        age_days = max(0.0, (as_of - match.match_date).total_seconds() / 86400.0)
        weight = 0.5 ** (age_days / half_life_days)
        weighted.append((match, weight))
    total_w = sum(weight for _, weight in weighted) or 1.0
    global_home = sum(match.home_goals * weight for match, weight in weighted) / total_w
    global_away = sum(match.away_goals * weight for match, weight in weighted) / total_w
    global_goal = max((global_home + global_away) / 2.0, 0.2)

    home_profile = _team_profile(weighted, home_team, global_goal, bayesian=bayesian)
    away_profile = _team_profile(weighted, away_team, global_goal, bayesian=bayesian)
    home_xg = global_home * home_profile["attack"] * away_profile["defense"]
    away_xg = global_away * away_profile["attack"] * home_profile["defense"]
    covariance = sum(
        weight
        * (match.home_goals - global_home)
        * (match.away_goals - global_away)
        for match, weight in weighted
    ) / total_w
    shared_lambda = min(0.35, max(0.0, covariance))
    return {
        "home_xg": round(_clamp(home_xg, 0.15, 4.5), 6),
        "away_xg": round(_clamp(away_xg, 0.15, 4.5), 6),
        "shared_lambda": round(shared_lambda, 6),
        "shared_lambda_method": "weighted_goal_covariance_moment_estimator",
        "history_count": len(history),
        "half_life_days": half_life_days,
        "bayesian_shrinkage": bayesian,
        "evolution_method": (
            "time_decay_empirical_bayes_shrinkage" if bayesian else "weighted_time_decay"
        ),
        "home_team_profile": home_profile,
        "away_team_profile": away_profile,
    }


def _team_profile(
    weighted: list[tuple[HistoricalMatch, float]],
    team_name: str,
    global_goal: float,
    *,
    bayesian: bool,
) -> dict[str, float]:
    gf = 0.0
    ga = 0.0
    wsum = 0.0
    norm_team = _norm(team_name)
    for match, weight in weighted:
        if _norm(match.home_team) == norm_team:
            gf += match.home_goals * weight
            ga += match.away_goals * weight
            wsum += weight
        elif _norm(match.away_team) == norm_team:
            gf += match.away_goals * weight
            ga += match.home_goals * weight
            wsum += weight
    if bayesian:
        prior_w = 8.0
        gf += global_goal * prior_w
        ga += global_goal * prior_w
        wsum += prior_w
    if wsum <= 0:
        return {"attack": 1.0, "defense": 1.0, "weighted_matches": 0.0}
    attack = (gf / wsum) / global_goal
    defense = (ga / wsum) / global_goal
    return {
        "attack": _clamp(attack, 0.35, 2.4),
        "defense": _clamp(defense, 0.35, 2.4),
        "weighted_matches": round(wsum, 4),
    }


def _independent_poisson_matrix(
    home_xg: float,
    away_xg: float,
    max_goals: int = 10,
) -> list[list[float]]:
    matrix = [
        [_poisson_pmf(h, home_xg) * _poisson_pmf(a, away_xg) for a in range(max_goals + 1)]
        for h in range(max_goals + 1)
    ]
    total = sum(sum(row) for row in matrix) or 1.0
    return [[value / total for value in row] for row in matrix]


def _bivariate_poisson_matrix(
    home_xg: float,
    away_xg: float,
    *,
    shared_lambda: float,
    max_goals: int = 10,
) -> list[list[float]]:
    lam3 = min(shared_lambda, home_xg * 0.35, away_xg * 0.35)
    lam1 = max(home_xg - lam3, 1e-6)
    lam2 = max(away_xg - lam3, 1e-6)
    mat = []
    base = math.exp(-(lam1 + lam2 + lam3))
    for h in range(max_goals + 1):
        row = []
        for a in range(max_goals + 1):
            prob = 0.0
            for k in range(min(h, a) + 1):
                prob += (
                    (lam1 ** (h - k) / math.factorial(h - k))
                    * (lam2 ** (a - k) / math.factorial(a - k))
                    * (lam3 ** k / math.factorial(k))
                )
            row.append(base * prob)
        mat.append(row)
    total = sum(sum(row) for row in mat) or 1.0
    return [[value / total for value in row] for row in mat]


def _matrix_to_probs(matrix: list[list[float]]) -> dict[str, float]:
    home = draw = away = 0.0
    for h, row in enumerate(matrix):
        for a, value in enumerate(row):
            if h > a:
                home += value
            elif h == a:
                draw += value
            else:
                away += value
    return _normalize({"home": home, "draw": draw, "away": away})


def _dirichlet_like_calibration(
    candidate_name: str,
    row: dict[str, Any],
    registry_rows: list[dict[str, Any]],
) -> ShadowCandidateResult:
    previous = _previous_paired_rows(row, registry_rows)
    if len(previous) < 30:
        return ShadowCandidateResult(
            candidate_name,
            False,
            reason=f"insufficient_prior_paired_samples_{len(previous)}",
            payload={
                "minimum_samples": 30,
                "candidate_family": candidate_family(candidate_name),
                "shadow_only": True,
            },
        )
    from sklearn.linear_model import LogisticRegression

    features: list[list[float]] = []
    targets: list[int] = []
    for previous_row in previous:
        actual = _actual_index(
            previous_row.get("actual_home_goals"),
            previous_row.get("actual_away_goals"),
        )
        if actual is None:
            continue
        probs = _normalize(previous_row["current_probs"])
        features.append([math.log(max(probs[key], 1e-6)) for key in ("home", "draw", "away")])
        targets.append(actual)
    if len(set(targets)) < 3:
        return ShadowCandidateResult(
            candidate_name,
            False,
            reason="prior_samples_missing_one_or_more_outcome_classes",
            payload={"prior_samples": len(targets), "shadow_only": True},
        )
    calibrator = LogisticRegression(C=0.1, max_iter=2000, solver="lbfgs")
    calibrator.fit(np.asarray(features, dtype=float), np.asarray(targets, dtype=int))
    current = _normalize(row["current_probs"])
    current_features = np.asarray(
        [[math.log(max(current[key], 1e-6)) for key in ("home", "draw", "away")]],
        dtype=float,
    )
    raw_prediction = calibrator.predict_proba(current_features)[0]
    calibrated = {"home": 0.0, "draw": 0.0, "away": 0.0}
    labels = ("home", "draw", "away")
    for class_id, probability in zip(calibrator.classes_, raw_prediction, strict=True):
        calibrated[labels[int(class_id)]] = float(probability)
    return ShadowCandidateResult(
        candidate_name,
        True,
        probs=_normalize(calibrated),
        reason="multinomial_log_probability_calibration",
        payload={
            "prior_samples": len(previous),
            "model_kind": "dirichlet_style_multinomial_log_calibration",
            "regularization_c": 0.1,
            "classes": calibrator.classes_.tolist(),
            "candidate_family": candidate_family(candidate_name),
            "shadow_only": True,
        },
    )


def _stacking_optimizer(
    candidate_name: str,
    row: dict[str, Any],
    registry_rows: list[dict[str, Any]],
) -> ShadowCandidateResult:
    previous = _previous_paired_rows(row, registry_rows)
    if len(previous) < 50:
        return ShadowCandidateResult(
            candidate_name,
            False,
            reason=f"insufficient_prior_stacking_samples_{len(previous)}",
            payload={
                "minimum_samples": 50,
                "candidate_family": candidate_family(candidate_name),
                "shadow_only": True,
            },
        )
    from sklearn.linear_model import LogisticRegression

    training_features: list[list[float]] = []
    targets: list[int] = []
    for previous_row in previous:
        feature = _stacking_features(previous_row.get("component_probs"))
        actual = _actual_index(
            previous_row.get("actual_home_goals"),
            previous_row.get("actual_away_goals"),
        )
        if feature is None or actual is None:
            continue
        training_features.append(feature)
        targets.append(actual)
    current_features = _stacking_features(row.get("component_probs"))
    if current_features is None:
        return ShadowCandidateResult(
            candidate_name,
            False,
            reason="current_component_probabilities_unavailable",
        )
    if len(training_features) < 50 or len(set(targets)) < 3:
        return ShadowCandidateResult(
            candidate_name,
            False,
            reason=f"insufficient_component_training_rows_{len(training_features)}",
            payload={"prior_samples": len(previous), "shadow_only": True},
        )
    learner = LogisticRegression(C=0.1, max_iter=2000, solver="lbfgs")
    learner.fit(np.asarray(training_features), np.asarray(targets))
    predicted = learner.predict_proba(np.asarray([current_features]))[0]
    labels = ("home", "draw", "away")
    probs = {label: 0.0 for label in labels}
    for class_id, probability in zip(learner.classes_, predicted, strict=True):
        probs[labels[int(class_id)]] = float(probability)
    return ShadowCandidateResult(
        candidate_name,
        True,
        probs=_normalize(probs),
        reason="expanding_window_multinomial_component_stacking",
        payload={
            "prior_samples": len(previous),
            "training_rows": len(training_features),
            "model_kind": "multinomial_logistic_component_stacking",
            "candidate_family": candidate_family(candidate_name),
            "shadow_only": True,
        },
    )


def _stacking_features(raw_components: Any) -> list[float] | None:
    if not isinstance(raw_components, dict):
        return None
    aliases = {
        "dc": ("dc", "dixon_coles"),
        "enhancer": ("enhancer", "tabular_enhancer"),
        "negbin": ("negbin", "negative_binomial"),
        "weibull": ("weibull",),
        "elo": ("elo", "elo_davidson"),
        "pi": ("pi", "pi_rating"),
        "market": ("market", "market_probs", "odds_snapshot"),
    }
    feature: list[float] = []
    available_count = 0
    for names in aliases.values():
        value = next((raw_components.get(name) for name in names if raw_components.get(name)), None)
        triplet = _component_triplet(value)
        if triplet is None:
            feature.extend((1 / 3, 1 / 3, 1 / 3, 0.0))
        else:
            available_count += 1
            feature.extend((triplet["home"], triplet["draw"], triplet["away"], 1.0))
    return feature if available_count >= 3 else None


def _component_triplet(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    candidates = {
        "home": raw.get("home", raw.get("home_win_prob", raw.get("home_prob"))),
        "draw": raw.get("draw", raw.get("draw_prob")),
        "away": raw.get("away", raw.get("away_win_prob", raw.get("away_prob"))),
    }
    if any(value is None for value in candidates.values()):
        return None
    return _normalize(candidates)


def _covariate_hybrid_candidate(
    candidate_name: str,
    row: dict[str, Any],
    *,
    db_path: str | Path,
    registry_rows: list[dict[str, Any]],
) -> ShadowCandidateResult:
    feature_count = _feature_snapshot_count(db_path)
    previous = _previous_paired_rows(row, registry_rows)
    payload = {
        "candidate_family": candidate_family(candidate_name),
        "minimum_feature_snapshots": 50,
        "feature_snapshot_count": feature_count,
        "minimum_prior_paired_samples": 50,
        "prior_paired_samples": len(previous),
        "shadow_only": True,
    }
    if feature_count < 50:
        return ShadowCandidateResult(
            candidate_name,
            False,
            reason=f"insufficient_feature_snapshots_{feature_count}",
            payload=payload,
        )
    if len(previous) < 50:
        return ShadowCandidateResult(
            candidate_name,
            False,
            reason=f"insufficient_prior_paired_samples_{len(previous)}",
            payload=payload,
        )
    return ShadowCandidateResult(
        candidate_name,
        False,
        reason="covariate_training_pipeline_not_materialized",
        payload=payload,
    )


def _feature_snapshot_count(db_path: str | Path) -> int:
    path = Path(db_path)
    if not path.exists():
        return 0
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(path))
        row = conn.execute(
            "SELECT COUNT(*) FROM feature_snapshots",
        ).fetchone()
        return int(row[0] or 0)
    except sqlite3.Error:
        return 0
    finally:
        if conn is not None:
            conn.close()


def _previous_paired_rows(row: dict[str, Any], registry_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    as_of = _parse_dt(row.get("as_of_time") or row.get("kickoff_at") or row.get("match_date"))
    if as_of is None:
        return []
    previous = []
    required_cohort = str(row.get("model_cohort") or "unknown")
    for candidate in registry_rows:
        if not candidate.get("eligible_for_backtest") or not isinstance(candidate.get("current_probs"), dict):
            continue
        if str(candidate.get("model_cohort") or "unknown") != required_cohort:
            continue
        c_time = _parse_dt(candidate.get("kickoff_at") or candidate.get("match_date"))
        if c_time is not None and c_time < as_of:
            previous.append(candidate)
    return previous


def _actual_index(home_goals: Any, away_goals: Any) -> int | None:
    if home_goals is None or away_goals is None:
        return None
    home = int(home_goals)
    away = int(away_goals)
    if home > away:
        return 0
    if home == away:
        return 1
    return 2


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _normalize(raw: dict[str, Any]) -> dict[str, float]:
    home = float(raw.get("home", raw.get("home_win_prob", 1 / 3)))
    draw = float(raw.get("draw", raw.get("draw_prob", 1 / 3)))
    away = float(raw.get("away", raw.get("away_win_prob", 1 / 3)))
    total = home + draw + away
    if total <= 0:
        return {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}
    return {"home": home / total, "draw": draw / total, "away": away / total}


def _parse_dt(raw: Any) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip()
    if len(text) == 10:
        text = f"{text}T23:59:59+00:00"
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _norm(raw: str) -> str:
    return " ".join(str(raw or "").strip().lower().split())


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
