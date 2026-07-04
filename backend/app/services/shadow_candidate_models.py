"""Shadow-only candidate probability models for V4.8 experiments.

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


SUPPORTED_SHADOW_CANDIDATES = {
    "uniform_baseline",
    "current_fusion",
    "dynamic_dixon_coles",
    "dynamic_bivariate_poisson",
    "bayesian_weighted_dynamic",
    "covariate_ml_baseline",
    "dirichlet_calibration",
    "stacking_optimizer",
}


@dataclass(frozen=True)
class ShadowCandidateResult:
    candidate_name: str
    available: bool
    probs: dict[str, float] | None = None
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

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
    if candidate_name == "uniform_baseline":
        return ShadowCandidateResult(
            candidate_name,
            True,
            probs={"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3},
            reason="uniform_reference",
        )
    if candidate_name == "current_fusion":
        current = row.get("current_probs")
        if not isinstance(current, dict):
            return ShadowCandidateResult(candidate_name, False, reason="missing_current_probs")
        return ShadowCandidateResult(candidate_name, True, probs=_normalize(current), reason="identity_champion")

    kickoff = _parse_dt(row.get("kickoff_at") or row.get("match_date"))
    if kickoff is None:
        return ShadowCandidateResult(candidate_name, False, reason="kickoff_time_unavailable")

    if candidate_name in {
        "dynamic_dixon_coles",
        "dynamic_bivariate_poisson",
        "bayesian_weighted_dynamic",
    }:
        history = _load_history(db_path, before=kickoff)
        if len(history) < 100:
            return ShadowCandidateResult(
                candidate_name,
                False,
                reason=f"insufficient_history_{len(history)}",
                payload={"history_count": len(history)},
            )
        lambdas = _dynamic_lambdas(
            history,
            home_team=str(row["home_team"]),
            away_team=str(row["away_team"]),
            as_of=kickoff,
            bayesian=candidate_name == "bayesian_weighted_dynamic",
        )
        if candidate_name == "dynamic_bivariate_poisson":
            probs = _bivariate_poisson_probs(
                lambdas["home_xg"],
                lambdas["away_xg"],
                shared_lambda=lambdas["shared_lambda"],
            )
        else:
            probs = _independent_poisson_probs(lambdas["home_xg"], lambdas["away_xg"])
        return ShadowCandidateResult(candidate_name, True, probs=probs, reason="computed_from_pre_match_history", payload=lambdas)

    if candidate_name == "dirichlet_calibration":
        return _dirichlet_like_calibration(candidate_name, row, registry_rows or [])

    if candidate_name == "stacking_optimizer":
        return _stacking_optimizer(candidate_name, row, registry_rows or [])

    if candidate_name == "covariate_ml_baseline":
        return ShadowCandidateResult(
            candidate_name,
            False,
            reason="requires_feature_snapshot_training_set",
            payload={"minimum_feature_snapshots": 50},
        )

    return ShadowCandidateResult(candidate_name, False, reason="not_implemented")


def _load_history(db_path: str | Path, *, before: datetime) -> list[HistoricalMatch]:
    path = Path(db_path)
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
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
            """
        ).fetchall()
    finally:
        conn.close()
    history = []
    for item in rows:
        match_dt = _parse_dt(item["match_date"])
        if match_dt is None or match_dt >= before:
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


def _dynamic_lambdas(
    history: list[HistoricalMatch],
    *,
    home_team: str,
    away_team: str,
    as_of: datetime,
    bayesian: bool,
) -> dict[str, float]:
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
    draw_rate = sum(
        weight for match, weight in weighted
        if match.home_goals == match.away_goals
    ) / total_w
    shared_lambda = min(0.18, max(0.02, draw_rate * 0.18))
    return {
        "home_xg": round(_clamp(home_xg, 0.15, 4.5), 6),
        "away_xg": round(_clamp(away_xg, 0.15, 4.5), 6),
        "shared_lambda": round(shared_lambda, 6),
        "history_count": len(history),
        "half_life_days": half_life_days,
        "bayesian_shrinkage": bayesian,
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


def _independent_poisson_probs(home_xg: float, away_xg: float, max_goals: int = 8) -> dict[str, float]:
    mat = [
        [_poisson_pmf(h, home_xg) * _poisson_pmf(a, away_xg) for a in range(max_goals + 1)]
        for h in range(max_goals + 1)
    ]
    return _matrix_to_probs(mat)


def _bivariate_poisson_probs(
    home_xg: float,
    away_xg: float,
    *,
    shared_lambda: float,
    max_goals: int = 8,
) -> dict[str, float]:
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
    return _matrix_to_probs(mat)


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
            payload={"minimum_samples": 30},
        )
    # Conservative multiclass calibration proxy: learn only a scalar shrinkage
    # toward uniform from prior log-loss, never a full Dirichlet parameter set.
    current = _normalize(row["current_probs"])
    shrink = _calibration_shrinkage(previous)
    calibrated = {
        key: current[key] * (1 - shrink) + (1 / 3) * shrink
        for key in ("home", "draw", "away")
    }
    return ShadowCandidateResult(
        candidate_name,
        True,
        probs=_normalize(calibrated),
        reason="scalar_multiclass_shrinkage_from_prior_samples",
        payload={"prior_samples": len(previous), "shrinkage": shrink},
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
            payload={"minimum_samples": 50},
        )
    return ShadowCandidateResult(
        candidate_name,
        False,
        reason="component_level_training_payload_unavailable",
        payload={"prior_samples": len(previous)},
    )


def _previous_paired_rows(row: dict[str, Any], registry_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    as_of = _parse_dt(row.get("as_of_time") or row.get("kickoff_at") or row.get("match_date"))
    if as_of is None:
        return []
    previous = []
    for candidate in registry_rows:
        if not candidate.get("eligible_for_backtest") or not isinstance(candidate.get("current_probs"), dict):
            continue
        c_time = _parse_dt(candidate.get("kickoff_at") or candidate.get("match_date"))
        if c_time is not None and c_time < as_of:
            previous.append(candidate)
    return previous


def _calibration_shrinkage(rows: list[dict[str, Any]]) -> float:
    losses = []
    for row in rows:
        actual = _actual_index(row.get("actual_home_goals"), row.get("actual_away_goals"))
        if actual is None:
            continue
        probs = _normalize(row["current_probs"])
        losses.append(-math.log(max([probs["home"], probs["draw"], probs["away"]][actual], 1e-12)))
    if not losses:
        return 0.0
    mean_loss = float(np.mean(losses))
    uniform_loss = -math.log(1 / 3)
    if mean_loss <= uniform_loss:
        return 0.0
    return round(_clamp((mean_loss - uniform_loss) / 2.0, 0.0, 0.20), 6)


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
