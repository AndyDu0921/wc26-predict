from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import Match, MatchResult, Team

logger = logging.getLogger(__name__)

# --- Cold-start configuration ---
# Minimum matches required before a team is considered "warm"
COLD_START_MIN_MATCHES = 5

# FIFA ranking tier modifiers for cold-start attack/defense scaling
# Applied as multiplier on confederation-average parameters
FIFA_TIER_MODIFIER = {
    1: 1.15,   # Top 10: strong attack, weak defense (relative to conf avg)
    2: 1.05,   # 11-30
    3: 0.95,   # 31-50
    4: 0.88,   # 51+
    0: 1.00,   # Unknown tier — no modification
}

# FIFA ranking mapping for 2026 World Cup qualified teams
# Tier 1 (top 10), Tier 2 (11-30), Tier 3 (31-50), Tier 4 (51+)
# Based on May 2026 approximate FIFA rankings
WC26_FIFA_TIERS: dict[str, int] = {
    # CONMEBOL
    "Argentina": 1, "Brazil": 1, "Uruguay": 1,
    "Colombia": 1, "Ecuador": 2, "Chile": 3,
    "Peru": 3, "Paraguay": 3, "Bolivia": 4, "Venezuela": 4,
    # UEFA
    "France": 1, "England": 1, "Spain": 1, "Portugal": 1,
    "Netherlands": 1, "Germany": 1, "Belgium": 1, "Croatia": 1,
    "Italy": 1, "Denmark": 2, "Switzerland": 2, "Austria": 2,
    "Serbia": 2, "Ukraine": 2, "Sweden": 2, "Turkey": 2,
    "Poland": 2, "Wales": 3, "Hungary": 3, "Czechia": 3,
    "Norway": 3, "Scotland": 3, "Romania": 3, "Slovakia": 3,
    "Greece": 3, "Slovenia": 4, "Bulgaria": 4, "Finland": 4,
    # CAF
    "Morocco": 2, "Senegal": 2, "Egypt": 2, "Nigeria": 2,
    "Cameroon": 3, "Côte d'Ivoire": 3, "Ghana": 3,
    "Tunisia": 3, "Algeria": 3, "South Africa": 3,
    "Mali": 3, "Burkina Faso": 4, "Burundi": 4, "DR Congo": 4, "Guinea": 4,
    # AFC
    "Japan": 2, "Iran": 2, "South Korea": 2, "Australia": 2,
    "Qatar": 3, "Saudi Arabia": 3, "United Arab Emirates": 3,
    "Uzbekistan": 3, "Iraq": 3, "China PR": 4, "Jordan": 4,
    "Oman": 4, "Bahrain": 4,
    # CONCACAF
    "United States": 2, "Mexico": 2, "Canada": 2, "Costa Rica": 3,
    "Panama": 3, "Jamaica": 3, "Honduras": 4, "El Salvador": 4,
    # OFC
    "New Zealand": 4, "Tahiti": 4, "Fiji": 4, "Solomon Islands": 4,
}

# Canonical confederation name normalization
CONFEDERATION_NORM = {
    "uefa": "UEFA", "afc": "AFC", "caf": "CAF",
    "conmebol": "CONMEBOL", "concacaf": "CONCACAF", "ofc": "OFC",
    "fifa": None,  # "FIFA" means unknown
}


# ── Vectorized Dixon-Coles negative log-likelihood ──────────────────
# Standalone function so L-BFGS-B can call it without object overhead.
# Pre-computed index arrays are passed by fit() via args=.


def _neg_log_likelihood_vectorized(
    params: np.ndarray,
    home_idx: np.ndarray,
    away_idx: np.ndarray,
    h_arr: np.ndarray,
    a_arr: np.ndarray,
    w_arr: np.ndarray,
    neutral_arr: np.ndarray,
    n_teams: int,
    team_counts: np.ndarray | None = None,
) -> float:
    """Vectorized Dixon-Coles NLL — ~30-100× faster than row-wise Python loop.

    Parameters
    ----------
    params : (2*n_teams + 2,)  log-attack, log-defense, home_adv, rho_raw
    home_idx / away_idx : int32 arrays mapping each row → team index
    h_arr / a_arr : int32 home / away goals per match
    w_arr : float64 pre-computed weight per match (time_decay × competition_weight)
    neutral_arr : bool array (True = neutral venue, skip home advantage)
    n_teams : number of teams in the training set
    team_counts : per-team match count for Bayesian shrinkage (or None)
    """
    # ── Unpack & transform parameters ──
    attack_logs = params[:n_teams]
    defense_logs = params[n_teams : n_teams * 2]
    raw_home_advantage = params[-2]
    raw_rho = params[-1]

    attack = np.exp(attack_logs)
    attack /= attack.mean()
    defense = np.exp(defense_logs)
    defense /= defense.mean()
    rho = np.tanh(raw_rho)

    # ── Expected goals (vectorised, one-shot for all matches) ──
    lam = attack[home_idx] * defense[away_idx]            # λ = home_att × away_def
    mu = attack[away_idx] * defense[home_idx]             # μ = away_att × home_def

    # Home-advantage multiplier (only for non-neutral matches)
    ha_factor = np.where(neutral_arr, 1.0, np.exp(raw_home_advantage))
    lam = lam * ha_factor

    # Clip to prevent log(0)
    lam = np.maximum(lam, 1e-8)
    mu = np.maximum(mu, 1e-8)

    # ── τ (Dixon-Coles low-score correction) ──
    tau = np.ones(len(h_arr), dtype=np.float64)
    m00 = (h_arr == 0) & (a_arr == 0)
    m10 = (h_arr == 1) & (a_arr == 0)
    m01 = (h_arr == 0) & (a_arr == 1)
    m11 = (h_arr == 1) & (a_arr == 1)
    tau[m00] = 1.0 - lam[m00] * mu[m00] * rho
    tau[m10] = 1.0 + mu[m10] * rho
    tau[m01] = 1.0 + lam[m01] * rho
    tau[m11] = 1.0 - rho

    # Hard penalty if any τ drops ≤ 0 — matches original behaviour
    if np.any(tau <= 0.0):
        return 1e9

    # ── Poisson log-PMF (gammaln, same backend as original) ──
    home_ll = h_arr.astype(np.float64) * np.log(lam) - lam - gammaln(h_arr + 1)
    away_ll = a_arr.astype(np.float64) * np.log(mu) - mu - gammaln(a_arr + 1)
    log_lik_per_match = np.log(tau) + home_ll + away_ll

    total = float(np.dot(w_arr, log_lik_per_match))

    # ── Bayesian prior shrinkage (L2 on log-params) ──
    if team_counts is not None and len(team_counts) > 0:
        min_matches = float(np.min(team_counts))
        prior_scale = max(1.0, min_matches * 0.5)
        shrink_weight = prior_scale / np.maximum(team_counts, 1.0)
        total += float(np.dot(shrink_weight, attack_logs**2 + defense_logs**2))

    return -total


@dataclass(slots=True)
class FitSummary:
    parameter_count: int
    final_neg_log_likelihood: float
    converged: bool
    message: str


class DixonColesModel:
    def __init__(self, half_life_days: int = 180) -> None:
        self.half_life_days = half_life_days
        self.attack_params: dict[str, float] = {}
        self.defense_params: dict[str, float] = {}
        self.home_advantage = 0.0
        self.rho = 0.0
        self.trained_at: datetime | None = None
        self._team_order: list[str] = []
        # Cold-start support
        self.conf_attack_avg: dict[str, float] = {}
        self.conf_defense_avg: dict[str, float] = {}
        self.team_info: dict[str, dict[str, Any]] = {}

    def set_team_info(self, team_info: dict[str, dict[str, Any]]) -> None:
        """Register team metadata for cold-start fallback.

        team_info: {team_name: {"confederation": "UEFA", "fifa_tier": 1}}
        Used when predict_match() encounters a team not in the fitted params.
        """
        self.team_info = team_info

    def _get_confederation(self, team_name: str) -> str | None:
        info = self.team_info.get(team_name, {})
        conf = info.get("confederation", "").upper()
        return CONFEDERATION_NORM.get(conf.lower(), conf if conf else None)

    def _get_fifa_tier(self, team_name: str) -> int:
        info = self.team_info.get(team_name, {})
        tier = info.get("fifa_tier")
        if tier is not None:
            return int(tier)
        # Fall back to static mapping
        return WC26_FIFA_TIERS.get(team_name, 0)

    def _is_cold_start(self, team_name: str) -> bool:
        """Return True if team has insufficient training data."""
        return team_name not in self.attack_params

    def _cold_start_estimate(
        self, team_name: str
    ) -> tuple[float, float, dict[str, Any]]:
        """Estimate attack/defense for a team lacking training data.

        Uses confederation-average parameters scaled by FIFA ranking tier.
        Returns (attack, defense, metadata).
        """
        conf = self._get_confederation(team_name)
        tier = self._get_fifa_tier(team_name)
        modifier = FIFA_TIER_MODIFIER.get(tier, 1.0)

        if conf and conf in self.conf_attack_avg:
            attack = self.conf_attack_avg[conf] * modifier
            defense = self.conf_defense_avg[conf] * (2.0 - modifier)
        else:
            # Global fallback: neutral attack/defense = 1.0
            attack = 1.0 * modifier
            defense = 1.0 * (2.0 - modifier)

        meta = {
            "data_quality": "estimated_prior",
            "confederation": conf or "unknown",
            "fifa_tier": tier,
            "tier_modifier": modifier,
            "cold_start": True,
        }
        return float(attack), float(defense), meta

    def _time_weight(self, match_date: date, reference_date: date) -> float:
        days = max(0, (reference_date - match_date).days)
        return math.exp(-math.log(2) * days / self.half_life_days)

    def _tau(self, x: int, y: int, lambda_: float, mu_: float, rho: float) -> float:
        if x == 0 and y == 0:
            return 1 - (lambda_ * mu_ * rho)
        if x == 0 and y == 1:
            return 1 + (lambda_ * rho)
        if x == 1 and y == 0:
            return 1 + (mu_ * rho)
        if x == 1 and y == 1:
            return 1 - rho
        return 1.0

    @staticmethod
    def _poisson_log_pmf(goals: int, rate: float) -> float:
        rate = max(rate, 1e-8)
        return goals * math.log(rate) - rate - gammaln(goals + 1)

    def _unpack_params(self, params: np.ndarray) -> tuple[dict[str, float], dict[str, float], float, float]:
        team_count = len(self._team_order)
        attack_logs = params[:team_count]
        defense_logs = params[team_count : team_count * 2]
        raw_home_advantage = params[-2]
        raw_rho = params[-1]

        attack = np.exp(attack_logs)
        defense = np.exp(defense_logs)
        attack /= attack.mean()
        defense /= defense.mean()
        rho = math.tanh(raw_rho)

        attack_params = dict(zip(self._team_order, attack.tolist(), strict=False))
        defense_params = dict(zip(self._team_order, defense.tolist(), strict=False))
        return attack_params, defense_params, raw_home_advantage, rho

    def _rates_for_match(
        self,
        home_team: str,
        away_team: str,
        attack_params: dict[str, float],
        defense_params: dict[str, float],
        home_advantage: float,
        is_neutral_venue: bool,
    ) -> tuple[float, float]:
        lambda_ = attack_params[home_team] * defense_params[away_team]
        mu_ = attack_params[away_team] * defense_params[home_team]
        if not is_neutral_venue:
            lambda_ *= math.exp(home_advantage)
        return max(lambda_, 1e-8), max(mu_, 1e-8)

    def _log_likelihood(self, params: np.ndarray, matches_df: pd.DataFrame, team_counts: np.ndarray | None = None) -> float:
        attack_params, defense_params, home_advantage, rho = self._unpack_params(params)
        reference_date = matches_df["match_date"].max().date()

        total = 0.0
        for row in matches_df.itertuples(index=False):
            lambda_, mu_ = self._rates_for_match(
                row.home_team,
                row.away_team,
                attack_params,
                defense_params,
                home_advantage,
                bool(row.is_neutral_venue),
            )
            tau = self._tau(int(row.home_goals), int(row.away_goals), lambda_, mu_, rho)
            if tau <= 0:
                return 1e9

            weight = self._time_weight(row.match_date.date(), reference_date) * float(row.competition_weight)
            log_prob = (
                math.log(tau)
                + self._poisson_log_pmf(int(row.home_goals), lambda_)
                + self._poisson_log_pmf(int(row.away_goals), mu_)
            )
            total += weight * log_prob

        # --- Bayesian prior shrinkage ---
        # Teams with fewer matches get stronger L2 regularization toward 0
        # (log(attack)=0 → attack=1, i.e. league average).
        # Equivalent to Gaussian prior N(0, σ²) with σ² ∝ min_matches / team_count.
        if team_counts is not None and len(team_counts) > 0:
            min_matches = float(np.min(team_counts))
            prior_scale = max(1.0, min_matches * 0.5)  # shrinkage strength
            team_count = len(self._team_order)
            attack_logs = params[:team_count]
            defense_logs = params[team_count:team_count * 2]
            for i, count in enumerate(team_counts):
                weight = prior_scale / max(float(count), 1.0)
                total += weight * (attack_logs[i] ** 2 + defense_logs[i] ** 2)

        return -total

    def fit(self, matches_df: pd.DataFrame) -> FitSummary:
        required_columns = {
            "match_date",
            "home_team",
            "away_team",
            "home_goals",
            "away_goals",
            "competition_weight",
            "is_neutral_venue",
        }
        missing = required_columns - set(matches_df.columns)
        if missing:
            raise ValueError(f"Training dataframe missing columns: {sorted(missing)}")
        if matches_df.empty:
            raise ValueError("Training dataframe is empty")

        df = matches_df.copy()
        df["match_date"] = pd.to_datetime(df["match_date"], utc=True)
        self._team_order = sorted(set(df["home_team"]).union(df["away_team"]))
        team_count = len(self._team_order)
        team_to_idx = {t: i for i, t in enumerate(self._team_order)}

        # ── Pre-compute index arrays for vectorized likelihood ──
        home_idx = np.array([team_to_idx[t] for t in df["home_team"]], dtype=np.int32)
        away_idx = np.array([team_to_idx[t] for t in df["away_team"]], dtype=np.int32)
        h_arr = df["home_goals"].to_numpy(dtype=np.int32)
        a_arr = df["away_goals"].to_numpy(dtype=np.int32)
        neutral_arr = df["is_neutral_venue"].to_numpy(dtype=bool)

        # Pre-compute composite weight: time_decay × competition_weight
        # Use .normalize() so day-diff matches (date - date).days behaviour
        ref_date = df["match_date"].max().normalize()
        days = (ref_date - df["match_date"].dt.normalize()).dt.days.clip(lower=0).to_numpy(dtype=np.float64)
        time_w = np.exp(-np.log(2) * days / self.half_life_days)
        w_arr = time_w * df["competition_weight"].to_numpy(dtype=np.float64)

        # Compute per-team match counts for prior shrinkage
        home_counts = df.groupby("home_team").size()
        away_counts = df.groupby("away_team").size()
        team_counts = np.array([
            int(home_counts.get(t, 0) + away_counts.get(t, 0))
            for t in self._team_order
        ], dtype=float)

        initial = np.concatenate(
            [
                np.zeros(team_count),
                np.zeros(team_count),
                np.array([0.1, 0.0]),
            ]
        )
        bounds = [(math.log(0.2), math.log(5.0))] * (team_count * 2) + [(-1.0, 1.0), (-3.0, 3.0)]

        # ── Use vectorized likelihood (30-100× faster than row-wise loop) ──
        # Compute initial NLL to gauge improvement even on non-convergence
        initial_nll = float(_neg_log_likelihood_vectorized(
            initial, home_idx, away_idx, h_arr, a_arr, w_arr, neutral_arr, team_count, team_counts
        ))

        result = minimize(
            fun=_neg_log_likelihood_vectorized,
            x0=initial,
            args=(home_idx, away_idx, h_arr, a_arr, w_arr, neutral_arr, team_count, team_counts),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 5000, "maxfun": 50000},
        )

        improved = result.fun < initial_nll * 0.999  # any meaningful improvement
        params_valid = not (np.any(np.isnan(result.x)) or np.any(np.isinf(result.x)))

        if result.success or (improved and params_valid):
            if not result.success:
                logger.warning(
                    "Dixon-Coles optimizer did not converge (%s) but improved NLL "
                    "from %.1f to %.1f — accepting best parameters found.",
                    result.message, initial_nll, result.fun,
                )
            self.attack_params, self.defense_params, self.home_advantage, self.rho = self._unpack_params(result.x)
        else:
            logger.error(
                "Dixon-Coles optimizer failed (NLL %.1f -> %.1f, valid=%s). "
                "Keeping existing parameters to avoid corruption.",
                initial_nll, result.fun, params_valid,
            )

        self.trained_at = datetime.now(UTC)

        # Compute confederation-level priors for cold-start fallback
        self._compute_conf_priors()

        return FitSummary(
            parameter_count=len(result.x),
            final_neg_log_likelihood=float(result.fun),
            converged=bool(result.success),
            message=str(result.message),
        )

    def _compute_conf_priors(self) -> None:
        """Compute confederation-average attack/defense from fitted params."""
        conf_attack: dict[str, list[float]] = {}
        conf_defense: dict[str, list[float]] = {}

        for team_name in self._team_order:
            conf = self._get_confederation(team_name)
            if conf is None:
                continue
            conf_attack.setdefault(conf, []).append(self.attack_params[team_name])
            conf_defense.setdefault(conf, []).append(self.defense_params[team_name])

        self.conf_attack_avg = {}
        self.conf_defense_avg = {}
        for conf in conf_attack:
            if len(conf_attack[conf]) >= 2:  # Need at least 2 teams for meaningful avg
                self.conf_attack_avg[conf] = float(np.mean(conf_attack[conf]))
                self.conf_defense_avg[conf] = float(np.mean(conf_defense[conf]))
            else:
                # Single team in confederation — use global average
                self.conf_attack_avg[conf] = 1.0
                self.conf_defense_avg[conf] = 1.0

        # Ensure all 6 confederations have a value
        for conf in ("UEFA", "AFC", "CAF", "CONMEBOL", "CONCACAF", "OFC"):
            if conf not in self.conf_attack_avg:
                self.conf_attack_avg[conf] = 1.0
                self.conf_defense_avg[conf] = 1.0

    def _resolve_team_params(
        self, team_name: str
    ) -> tuple[float, float, dict[str, Any] | None]:
        """Resolve attack/defense params for a team, using cold-start if needed.

        Returns (attack, defense, cold_start_meta | None).
        """
        if team_name in self.attack_params:
            return self.attack_params[team_name], self.defense_params[team_name], None
        attack, defense, meta = self._cold_start_estimate(team_name)
        return attack, defense, meta

    def predict_score_matrix(
        self,
        home_team: str,
        away_team: str,
        is_neutral_venue: bool = True,
        max_goals: int = 5,
    ) -> tuple[np.ndarray, list[dict[str, Any]]]:
        """Return (score_matrix, cold_start_warnings)."""
        home_attack, home_defense, home_cs = self._resolve_team_params(home_team)
        away_attack, away_defense, away_cs = self._resolve_team_params(away_team)

        lambda_ = home_attack * away_defense
        mu_ = away_attack * home_defense
        if not is_neutral_venue:
            lambda_ *= math.exp(self.home_advantage)
        lambda_ = max(lambda_, 1e-8)
        mu_ = max(mu_, 1e-8)

        matrix = np.zeros((max_goals + 1, max_goals + 1))
        for home_goals in range(max_goals + 1):
            for away_goals in range(max_goals + 1):
                prob = math.exp(self._poisson_log_pmf(home_goals, lambda_) + self._poisson_log_pmf(away_goals, mu_))
                prob *= self._tau(home_goals, away_goals, lambda_, mu_, self.rho)
                matrix[home_goals, away_goals] = max(prob, 0.0)
        total = matrix.sum()
        matrix = matrix / total if total > 0 else matrix

        cold_start_warnings = []
        if home_cs:
            cold_start_warnings.append({"team": home_team, "role": "home", **home_cs})
        if away_cs:
            cold_start_warnings.append({"team": away_team, "role": "away", **away_cs})

        return matrix, cold_start_warnings

    def predict_match(
        self,
        home_team: str,
        away_team: str,
        is_neutral_venue: bool = True,
    ) -> dict[str, Any]:
        matrix, cold_start_warnings = self.predict_score_matrix(
            home_team, away_team, is_neutral_venue=is_neutral_venue
        )
        home_win_prob = float(np.tril(matrix, -1).sum())
        draw_prob = float(np.trace(matrix))
        away_win_prob = float(np.triu(matrix, 1).sum())
        home_xg = float(sum(i * matrix[i, :].sum() for i in range(matrix.shape[0])))
        away_xg = float(sum(j * matrix[:, j].sum() for j in range(matrix.shape[1])))

        top_scores: list[dict[str, float | str]] = []
        flattened = []
        for i in range(matrix.shape[0]):
            for j in range(matrix.shape[1]):
                flattened.append((i, j, float(matrix[i, j])))
        for home_goals, away_goals, probability in sorted(flattened, key=lambda item: item[2], reverse=True)[:3]:
            top_scores.append({"score": f"{home_goals}:{away_goals}", "prob": probability})

        # Build cold-start metadata
        data_quality = "fitted" if not cold_start_warnings else "estimated_prior"
        confidence_penalty = 0.0
        risk_tags: list[str] = []
        if cold_start_warnings:
            confidence_penalty = 0.15
            risk_tags.append("训练数据不足，依赖先验估计")
            cs_teams = [w["team"] for w in cold_start_warnings]
            risk_tags.append(f"冷启动球队: {', '.join(cs_teams)}")

        model_params = {}
        if home_team in self.attack_params:
            model_params.update({
                "home_attack": self.attack_params[home_team],
                "home_defense": self.defense_params[home_team],
            })
        else:
            model_params["home_attack_estimated"] = True
        if away_team in self.attack_params:
            model_params.update({
                "away_attack": self.attack_params[away_team],
                "away_defense": self.defense_params[away_team],
            })
        else:
            model_params["away_attack_estimated"] = True
        model_params["rho"] = self.rho
        model_params["home_advantage_applied"] = not is_neutral_venue

        return {
            "home_win_prob": home_win_prob,
            "draw_prob": draw_prob,
            "away_win_prob": away_win_prob,
            "home_xg": home_xg,
            "away_xg": away_xg,
            "top3_scores": top_scores,
            "score_matrix": matrix.tolist(),
            "model_params_used": model_params,
            "data_quality": data_quality,
            "confidence_penalty": confidence_penalty,
            "risk_tags": risk_tags,
            "cold_start_warnings": cold_start_warnings,
        }

    def evaluate(self, test_matches_df: pd.DataFrame) -> dict[str, Any]:
        if test_matches_df.empty:
            raise ValueError("Evaluation dataframe is empty")

        df = test_matches_df.copy()
        df["match_date"] = pd.to_datetime(df["match_date"], utc=True)
        brier_scores: list[float] = []
        log_losses: list[float] = []
        exact_hits = 0
        top3_hits = 0
        buckets = {f"{bucket / 10:.1f}-{(bucket + 1) / 10:.1f}": [] for bucket in range(10)}

        for row in df.itertuples(index=False):
            prediction = self.predict_match(row.home_team, row.away_team, bool(row.is_neutral_venue))
            probs = np.array(
                [prediction["home_win_prob"], prediction["draw_prob"], prediction["away_win_prob"]],
                dtype=float,
            )
            actual_index = 0 if row.home_goals > row.away_goals else 1 if row.home_goals == row.away_goals else 2
            actual = np.zeros(3)
            actual[actual_index] = 1.0
            brier_scores.append(float(((probs - actual) ** 2).sum()))
            log_losses.append(float(-math.log(max(probs[actual_index], 1e-12))))

            predicted_top = prediction["top3_scores"][0]["score"]
            actual_score = f"{int(row.home_goals)}:{int(row.away_goals)}"
            exact_hits += int(predicted_top == actual_score)
            top3_hits += int(any(item["score"] == actual_score for item in prediction["top3_scores"]))

            confidence = float(probs.max())
            bucket_floor = min(9, int(confidence * 10))
            key = f"{bucket_floor / 10:.1f}-{(bucket_floor + 1) / 10:.1f}"
            buckets[key].append(int(np.argmax(probs) == actual_index))

        calibration = {
            bucket: (sum(values) / len(values) if values else 0.0)
            for bucket, values in buckets.items()
        }
        return {
            "brier_score": float(np.mean(brier_scores)),
            "log_loss": float(np.mean(log_losses)),
            "calibration": calibration,
            "exact_score_hit_rate": exact_hits / len(df),
            "top3_hit_rate": top3_hits / len(df),
        }

    def save(self, path: str) -> None:
        payload = {
            "attack_params": self.attack_params,
            "defense_params": self.defense_params,
            "home_advantage": self.home_advantage,
            "rho": self.rho,
            "half_life_days": self.half_life_days,
            "trained_at": self.trained_at.isoformat() if self.trained_at else None,
            "conf_attack_avg": self.conf_attack_avg,
            "conf_defense_avg": self.conf_defense_avg,
            "team_info": self.team_info,
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, path: str) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.attack_params = {key: float(value) for key, value in payload["attack_params"].items()}
        self.defense_params = {key: float(value) for key, value in payload["defense_params"].items()}
        self.home_advantage = float(payload["home_advantage"])
        self.rho = float(payload["rho"])
        self.half_life_days = int(payload.get("half_life_days", 180))
        self.trained_at = datetime.fromisoformat(payload["trained_at"]) if payload["trained_at"] else None
        self._team_order = sorted(self.attack_params)
        self.conf_attack_avg = {key: float(value) for key, value in payload.get("conf_attack_avg", {}).items()}
        self.conf_defense_avg = {key: float(value) for key, value in payload.get("conf_defense_avg", {}).items()}
        self.team_info = payload.get("team_info", {})


TRAINING_COLUMNS = [
    "match_date",
    "home_team",
    "away_team",
    "home_goals",
    "away_goals",
    "competition",
    "competition_type",
    "competition_weight",
    "is_neutral_venue",
    "home_xg",
    "away_xg",
]


async def load_training_frame(
    db: AsyncSession,
    *,
    as_of_time: datetime | None = None,
    competition: str | None = None,
    competitions: list[str] | None = None,
    competition_type: str | None = None,
    team_type: str | None = None,
    stages: list[str] | None = None,
) -> pd.DataFrame:
    home_team = aliased(Team)
    away_team = aliased(Team)
    stmt: Select[tuple[datetime, str, str, int, int, str, str, float, bool, float | None, float | None]] = (
        select(
            Match.match_date,
            home_team.name.label("home_team"),
            away_team.name.label("away_team"),
            MatchResult.home_goals,
            MatchResult.away_goals,
            Match.competition,
            Match.competition_type,
            Match.competition_weight,
            Match.is_neutral_venue,
            MatchResult.home_xg,
            MatchResult.away_xg,
        )
        .join(MatchResult, MatchResult.match_id == Match.id)
        .join(home_team, home_team.id == Match.home_team_id)
        .join(away_team, away_team.id == Match.away_team_id)
        .order_by(Match.match_date.asc())
    )

    if as_of_time is not None:
        stmt = stmt.where(Match.match_date < as_of_time)
    if competition:
        stmt = stmt.where(Match.competition == competition)
    if competitions:
        stmt = stmt.where(Match.competition.in_(competitions))
    if competition_type:
        stmt = stmt.where(Match.competition_type == competition_type)
    if team_type:
        stmt = stmt.where(home_team.team_type == team_type, away_team.team_type == team_type)
    if stages:
        stmt = stmt.where(Match.stage.in_(stages))

    result = await db.execute(stmt)
    rows = [
        {
            "match_date": match_date,
            "home_team": home_name,
            "away_team": away_name,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "competition": competition_name,
            "competition_type": competition_type_name,
            "competition_weight": competition_weight,
            "is_neutral_venue": is_neutral_venue,
            "home_xg": home_xg,
            "away_xg": away_xg,
        }
        for (
            match_date,
            home_name,
            away_name,
            home_goals,
            away_goals,
            competition_name,
            competition_type_name,
            competition_weight,
            is_neutral_venue,
            home_xg,
            away_xg,
        ) in result.all()
    ]
    return pd.DataFrame(rows, columns=TRAINING_COLUMNS)


def split_train_holdout_frame(
    matches_df: pd.DataFrame,
    *,
    holdout_ratio: float = 0.1,
    minimum_holdout_rows: int = 1,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if matches_df.empty:
        raise ValueError("Training dataframe is empty")

    holdout_size = max(minimum_holdout_rows, int(len(matches_df) * holdout_ratio))
    holdout_size = min(max(1, holdout_size), max(1, len(matches_df) - 1))
    train_df = matches_df.iloc[:-holdout_size].copy()
    holdout_df = matches_df.iloc[-holdout_size:].copy()
    return train_df, holdout_df
