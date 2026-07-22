from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier


OUTCOME_LABELS = ("H", "D", "A")


@dataclass(slots=True)
class TabularFitSummary:
    algorithm: str
    training_rows: int
    feature_count: int
    classes_seen: list[str]
    fitted: bool
    training_window_start: str | None
    training_window_end: str | None


def normalize_probability_triplet(probabilities: dict[str, float]) -> dict[str, float]:
    values = np.asarray(
        [
            max(0.0, float(probabilities["home_win_prob"])),
            max(0.0, float(probabilities["draw_prob"])),
            max(0.0, float(probabilities["away_win_prob"])),
        ],
        dtype=float,
    )
    total = float(values.sum())
    if total <= 0:
        values = np.asarray([1 / 3, 1 / 3, 1 / 3], dtype=float)
    else:
        values /= total
    return {
        "home_win_prob": float(values[0]),
        "draw_prob": float(values[1]),
        "away_win_prob": float(values[2]),
    }


def fuse_outcome_probabilities(
    base_probabilities: dict[str, float],
    enhancer_probabilities: dict[str, float] | None,
    *,
    base_weight: float = 0.7,
) -> dict[str, float]:
    normalized_base = normalize_probability_triplet(base_probabilities)
    if enhancer_probabilities is None:
        return normalized_base

    enhancer_weight = max(0.0, 1.0 - base_weight)
    normalized_enhancer = normalize_probability_triplet(enhancer_probabilities)
    fused = {
        key: normalized_base[key] * base_weight + normalized_enhancer[key] * enhancer_weight
        for key in normalized_base
    }
    return normalize_probability_triplet(fused)


class TabularMatchEnhancer:
    FEATURE_COLUMNS = [
        "is_neutral_venue",
        "competition_weight",
        "home_matches_played",
        "away_matches_played",
        "experience_gap",
        "home_goals_for_avg",
        "home_goals_against_avg",
        "away_goals_for_avg",
        "away_goals_against_avg",
        "goal_balance_gap",
        "home_xg_for_avg",
        "home_xg_against_avg",
        "away_xg_for_avg",
        "away_xg_against_avg",
        "xg_balance_gap",
        "home_recent_goals_for_avg",
        "home_recent_goals_against_avg",
        "away_recent_goals_for_avg",
        "away_recent_goals_against_avg",
        "recent_goal_gap",
        "home_recent_xg_for_avg",
        "home_recent_xg_against_avg",
        "away_recent_xg_for_avg",
        "away_recent_xg_against_avg",
        "recent_xg_gap",
        "home_points_per_match",
        "away_points_per_match",
        "points_gap",
        "home_recent_points_per_match",
        "away_recent_points_per_match",
        "recent_points_gap",
        "home_win_rate",
        "away_win_rate",
        "win_rate_gap",
        "home_rest_days",
        "away_rest_days",
        "rest_day_diff",
    ]

    def __init__(self, *, use_xgboost: bool = False) -> None:
        if use_xgboost:
            try:
                from xgboost import XGBClassifier
                self.model = XGBClassifier(
                    learning_rate=0.06,
                    max_depth=4,
                    n_estimators=220,
                    min_child_weight=4,
                    random_state=42,
                    verbosity=0,
                )
                self._algorithm = "XGBoost"
            except ImportError:
                self.model = HistGradientBoostingClassifier(
                    learning_rate=0.06,
                    max_depth=4,
                    max_iter=220,
                    min_samples_leaf=4,
                    random_state=42,
                )
                self._algorithm = "HistGradientBoostingClassifier (fallback)"
        else:
            self.model = HistGradientBoostingClassifier(
                learning_rate=0.06,
                max_depth=4,
                max_iter=220,
                min_samples_leaf=4,
                random_state=42,
            )
            self._algorithm = "HistGradientBoostingClassifier"
        self.feature_columns = list(self.FEATURE_COLUMNS)
        self.is_fitted = False
        self.fitted_at: datetime | None = None
        self.training_sample_count = 0
        self.fit_summary: TabularFitSummary | None = None

    def fit(self, matches_df: pd.DataFrame) -> TabularFitSummary:
        feature_frame, labels = self._build_training_dataset(matches_df)
        self.training_sample_count = len(feature_frame)
        if len(feature_frame) < 12:
            raise ValueError("Not enough rows to fit tabular enhancer")
        if len(set(labels.tolist())) < 2:
            raise ValueError("Tabular enhancer needs at least two outcome classes")

        self.model.fit(feature_frame[self.feature_columns], labels)
        self.is_fitted = True
        self.fitted_at = datetime.now(UTC)
        self.fit_summary = TabularFitSummary(
            algorithm="HistGradientBoostingClassifier",
            training_rows=len(feature_frame),
            feature_count=len(self.feature_columns),
            classes_seen=[OUTCOME_LABELS[index] for index in self.model.classes_.tolist()],
            fitted=True,
            training_window_start=self._format_timestamp(matches_df["match_date"].min()),
            training_window_end=self._format_timestamp(matches_df["match_date"].max()),
        )
        return self.fit_summary

    def predict_match(
        self,
        *,
        home_team: str,
        away_team: str,
        match_date: datetime,
        competition_weight: float,
        is_neutral_venue: bool,
        training_df: pd.DataFrame,
        rest_days: dict[str, int | None] | None = None,
    ) -> dict[str, Any]:
        if not self.is_fitted:
            raise ValueError("Tabular enhancer is not fitted")
        feature_row = self._build_feature_row(
            history_df=self._normalize_frame(training_df),
            home_team=home_team,
            away_team=away_team,
            match_date=match_date,
            competition_weight=competition_weight,
            is_neutral_venue=is_neutral_venue,
            rest_days=rest_days,
        )
        feature_frame = pd.DataFrame([feature_row], columns=self.feature_columns)
        raw_probabilities = self.model.predict_proba(feature_frame)[0]
        probability_map = {"home_win_prob": 0.0, "draw_prob": 0.0, "away_win_prob": 0.0}
        for class_index, class_label in enumerate(self.model.classes_.tolist()):
            key = (
                "home_win_prob"
                if OUTCOME_LABELS[class_label] == "H"
                else "draw_prob"
                if OUTCOME_LABELS[class_label] == "D"
                else "away_win_prob"
            )
            probability_map[key] = float(raw_probabilities[class_index])

        return {
            **normalize_probability_triplet(probability_map),
            "feature_snapshot": feature_row,
            "fit_summary": asdict(self.fit_summary) if self.fit_summary else None,
        }

    def _build_training_dataset(self, matches_df: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
        """Vectorized feature builder — computes rolling team stats via groupby-expanding.

        Replaces the original O(n²) row-by-row loop with O(n log n) pandas
        groupby + expanding window operations.  Training on 5988 rows now takes
        ~2-5 seconds instead of 30-60+.
        """
        df = self._normalize_frame(matches_df)
        if df.empty:
            return pd.DataFrame(columns=self.feature_columns), np.array([], dtype=int)

        # Compute rest days: days since each team's last match
        df["_idx"] = range(len(df))
        home_rest = self._compute_rest_days(df, "home_team", "match_date")
        away_rest = self._compute_rest_days(df, "away_team", "match_date")

        # Build team-level stats for both home and away sides
        home_stats = self._build_side_stats(df, "home")
        away_stats = self._build_side_stats(df, "away")

        features = pd.DataFrame({
            "is_neutral_venue": df["is_neutral_venue"].astype(float),
            "competition_weight": df["competition_weight"].astype(float),
            # Home stats
            "home_matches_played": home_stats["matches_played"],
            "away_matches_played": away_stats["matches_played"],
            "experience_gap": home_stats["matches_played"] - away_stats["matches_played"],
            "home_goals_for_avg": home_stats["goals_for_avg"],
            "home_goals_against_avg": home_stats["goals_against_avg"],
            "away_goals_for_avg": away_stats["goals_for_avg"],
            "away_goals_against_avg": away_stats["goals_against_avg"],
            "goal_balance_gap": (
                (home_stats["goals_for_avg"] - home_stats["goals_against_avg"])
                - (away_stats["goals_for_avg"] - away_stats["goals_against_avg"])
            ),
            "home_xg_for_avg": home_stats["xg_for_avg"],
            "home_xg_against_avg": home_stats["xg_against_avg"],
            "away_xg_for_avg": away_stats["xg_for_avg"],
            "away_xg_against_avg": away_stats["xg_against_avg"],
            "xg_balance_gap": (
                (home_stats["xg_for_avg"] - home_stats["xg_against_avg"])
                - (away_stats["xg_for_avg"] - away_stats["xg_against_avg"])
            ),
            # Recent (last 5 matches) stats
            "home_recent_goals_for_avg": home_stats["recent_goals_for_avg"],
            "home_recent_goals_against_avg": home_stats["recent_goals_against_avg"],
            "away_recent_goals_for_avg": away_stats["recent_goals_for_avg"],
            "away_recent_goals_against_avg": away_stats["recent_goals_against_avg"],
            "recent_goal_gap": (
                (home_stats["recent_goals_for_avg"] - home_stats["recent_goals_against_avg"])
                - (away_stats["recent_goals_for_avg"] - away_stats["recent_goals_against_avg"])
            ),
            "home_recent_xg_for_avg": home_stats["recent_xg_for_avg"],
            "home_recent_xg_against_avg": home_stats["recent_xg_against_avg"],
            "away_recent_xg_for_avg": away_stats["recent_xg_for_avg"],
            "away_recent_xg_against_avg": away_stats["recent_xg_against_avg"],
            "recent_xg_gap": (
                (home_stats["recent_xg_for_avg"] - home_stats["recent_xg_against_avg"])
                - (away_stats["recent_xg_for_avg"] - away_stats["recent_xg_against_avg"])
            ),
            # Points and win rate
            "home_points_per_match": home_stats["points_per_match"],
            "away_points_per_match": away_stats["points_per_match"],
            "points_gap": home_stats["points_per_match"] - away_stats["points_per_match"],
            "home_recent_points_per_match": home_stats["recent_points_per_match"],
            "away_recent_points_per_match": away_stats["recent_points_per_match"],
            "recent_points_gap": home_stats["recent_points_per_match"] - away_stats["recent_points_per_match"],
            "home_win_rate": home_stats["win_rate"],
            "away_win_rate": away_stats["win_rate"],
            "win_rate_gap": home_stats["win_rate"] - away_stats["win_rate"],
            # Rest days
            "home_rest_days": home_rest.clip(lower=2.0),
            "away_rest_days": away_rest.clip(lower=2.0),
            "rest_day_diff": home_rest.clip(lower=2.0) - away_rest.clip(lower=2.0),
        })

        labels = np.select(
            [df["home_goals"] > df["away_goals"], df["home_goals"] == df["away_goals"]],
            [0, 1],
            default=2,
        ).astype(int)

        return features, labels

    def _compute_rest_days(self, df: pd.DataFrame, team_col: str, date_col: str) -> pd.Series:
        """For each row, compute days since the same team's last prior match."""
        df = df.copy()
        df["_idx"] = range(len(df))
        team_dates = df[["_idx", team_col, date_col]].copy()
        team_dates[date_col] = pd.to_datetime(team_dates[date_col], utc=True)

        rest = pd.Series(7.0, index=df.index)
        for _, group in team_dates.groupby(team_col):
            sorted_group = group.sort_values(date_col)
            diffs = sorted_group[date_col].diff().dt.total_seconds() / 86400.0
            rest.loc[sorted_group["_idx"]] = diffs.fillna(7.0)
        return rest

    def _build_side_stats(self, df: pd.DataFrame, side: str) -> pd.DataFrame:
        """Build expanding-window team stats for one side (home/away).

        Returns a DataFrame indexed by match row with columns:
        matches_played, goals_for_avg, goals_against_avg, xg_for_avg, xg_against_avg,
        points_per_match, recent_points_per_match, win_rate,
        recent_goals_for_avg, recent_goals_against_avg,
        recent_xg_for_avg, recent_xg_against_avg.
        """
        team_col = "home_team" if side == "home" else "away_team"

        df = df.copy()
        df["_idx"] = range(len(df))
        df["match_date"] = pd.to_datetime(df["match_date"], utc=True)

        if side == "home":
            df["goals_for"] = df["home_goals"]
            df["goals_against"] = df["away_goals"]
            df["xg_for"] = df["home_xg"].fillna(df["home_goals"]).astype(float)
            df["xg_against"] = df["away_xg"].fillna(df["away_goals"]).astype(float)
            df["points"] = np.select(
                [df["home_goals"] > df["away_goals"], df["home_goals"] == df["away_goals"]],
                [3, 1], default=0,
            ).astype(float)
            df["win"] = (df["home_goals"] > df["away_goals"]).astype(float)
        else:
            df["goals_for"] = df["away_goals"]
            df["goals_against"] = df["home_goals"]
            df["xg_for"] = df["away_xg"].fillna(df["away_goals"]).astype(float)
            df["xg_against"] = df["home_xg"].fillna(df["home_goals"]).astype(float)
            df["points"] = np.select(
                [df["away_goals"] > df["home_goals"], df["away_goals"] == df["home_goals"]],
                [3, 1], default=0,
            ).astype(float)
            df["win"] = (df["away_goals"] > df["home_goals"]).astype(float)

        # Sort by date for correct expanding window
        df = df.sort_values([team_col, "match_date"])

        result = pd.DataFrame(index=df["_idx"])

        # Expanding window: all prior matches for this team
        for col in ["goals_for", "goals_against", "xg_for", "xg_against", "points", "win"]:
            ew = df.groupby(team_col)[col].expanding(min_periods=1)
            result[f"{col}_ew"] = ew.mean().shift(1).fillna(
                df.groupby(team_col)[col].transform("first")
            ).values

        # Rolling window: last 5 matches
        for col in ["goals_for", "goals_against", "xg_for", "xg_against", "points"]:
            rw = df.groupby(team_col)[col].rolling(5, min_periods=1)
            result[f"{col}_recent"] = rw.mean().shift(1).fillna(
                result[f"{col}_ew"]
            ).values

        result["matches_played"] = df.groupby(team_col).cumcount().values

        # Align back to original row order
        result = result.loc[df["_idx"].sort_values().index].sort_index()

        # Default values for first appearance of a team
        league_avg_goals = df["goals_for"].mean() if not df.empty else 1.3
        defaults = {
            "goals_for_ew": league_avg_goals,
            "goals_against_ew": league_avg_goals,
            "xg_for_ew": league_avg_goals,
            "xg_against_ew": league_avg_goals,
            "points_ew": 1.3,
            "win_ew": 0.33,
        }
        for col, val in defaults.items():
            result[col] = result[col].fillna(val)
        for col in ["goals_for_recent", "goals_against_recent", "xg_for_recent", "xg_against_recent", "points_recent"]:
            ew_col = col.replace("_recent", "_ew")
            result[col] = result[col].fillna(result[ew_col])

        result = result.rename(columns={
            "goals_for_ew": "goals_for_avg",
            "goals_against_ew": "goals_against_avg",
            "xg_for_ew": "xg_for_avg",
            "xg_against_ew": "xg_against_avg",
            "points_ew": "points_per_match",
            "win_ew": "win_rate",
            "goals_for_recent": "recent_goals_for_avg",
            "goals_against_recent": "recent_goals_against_avg",
            "xg_for_recent": "recent_xg_for_avg",
            "xg_against_recent": "recent_xg_against_avg",
            "points_recent": "recent_points_per_match",
        })
        return result[self._all_stat_columns()]

    def _all_stat_columns(self) -> list[str]:
        return [
            "matches_played", "goals_for_avg", "goals_against_avg",
            "xg_for_avg", "xg_against_avg", "points_per_match",
            "recent_points_per_match", "win_rate",
            "recent_goals_for_avg", "recent_goals_against_avg",
            "recent_xg_for_avg", "recent_xg_against_avg",
        ]

    def _build_feature_row(
        self,
        *,
        history_df: pd.DataFrame,
        home_team: str,
        away_team: str,
        match_date: datetime,
        competition_weight: float,
        is_neutral_venue: bool,
        rest_days: dict[str, int | None] | None,
    ) -> dict[str, float]:
        home_profile = self._team_profile(history_df, home_team, match_date)
        away_profile = self._team_profile(history_df, away_team, match_date)
        home_rest = float(home_profile["rest_days"] if rest_days is None
                          else (rest_days.get("home") if rest_days.get("home") is not None
                                else home_profile["rest_days"]))
        away_rest = float(away_profile["rest_days"] if rest_days is None
                          else (rest_days.get("away") if rest_days.get("away") is not None
                                else away_profile["rest_days"]))

        return {
            "is_neutral_venue": float(bool(is_neutral_venue)),
            "competition_weight": float(competition_weight),
            "home_matches_played": float(home_profile["matches_played"]),
            "away_matches_played": float(away_profile["matches_played"]),
            "experience_gap": float(home_profile["matches_played"] - away_profile["matches_played"]),
            "home_goals_for_avg": float(home_profile["goals_for_avg"]),
            "home_goals_against_avg": float(home_profile["goals_against_avg"]),
            "away_goals_for_avg": float(away_profile["goals_for_avg"]),
            "away_goals_against_avg": float(away_profile["goals_against_avg"]),
            "goal_balance_gap": float(
                (home_profile["goals_for_avg"] - home_profile["goals_against_avg"])
                - (away_profile["goals_for_avg"] - away_profile["goals_against_avg"])
            ),
            "home_xg_for_avg": float(home_profile["xg_for_avg"]),
            "home_xg_against_avg": float(home_profile["xg_against_avg"]),
            "away_xg_for_avg": float(away_profile["xg_for_avg"]),
            "away_xg_against_avg": float(away_profile["xg_against_avg"]),
            "xg_balance_gap": float(
                (home_profile["xg_for_avg"] - home_profile["xg_against_avg"])
                - (away_profile["xg_for_avg"] - away_profile["xg_against_avg"])
            ),
            "home_recent_goals_for_avg": float(home_profile["recent_goals_for_avg"]),
            "home_recent_goals_against_avg": float(home_profile["recent_goals_against_avg"]),
            "away_recent_goals_for_avg": float(away_profile["recent_goals_for_avg"]),
            "away_recent_goals_against_avg": float(away_profile["recent_goals_against_avg"]),
            "recent_goal_gap": float(
                (home_profile["recent_goals_for_avg"] - home_profile["recent_goals_against_avg"])
                - (away_profile["recent_goals_for_avg"] - away_profile["recent_goals_against_avg"])
            ),
            "home_recent_xg_for_avg": float(home_profile["recent_xg_for_avg"]),
            "home_recent_xg_against_avg": float(home_profile["recent_xg_against_avg"]),
            "away_recent_xg_for_avg": float(away_profile["recent_xg_for_avg"]),
            "away_recent_xg_against_avg": float(away_profile["recent_xg_against_avg"]),
            "recent_xg_gap": float(
                (home_profile["recent_xg_for_avg"] - home_profile["recent_xg_against_avg"])
                - (away_profile["recent_xg_for_avg"] - away_profile["recent_xg_against_avg"])
            ),
            "home_points_per_match": float(home_profile["points_per_match"]),
            "away_points_per_match": float(away_profile["points_per_match"]),
            "points_gap": float(home_profile["points_per_match"] - away_profile["points_per_match"]),
            "home_recent_points_per_match": float(home_profile["recent_points_per_match"]),
            "away_recent_points_per_match": float(away_profile["recent_points_per_match"]),
            "recent_points_gap": float(home_profile["recent_points_per_match"] - away_profile["recent_points_per_match"]),
            "home_win_rate": float(home_profile["win_rate"]),
            "away_win_rate": float(away_profile["win_rate"]),
            "win_rate_gap": float(home_profile["win_rate"] - away_profile["win_rate"]),
            "home_rest_days": home_rest,
            "away_rest_days": away_rest,
            "rest_day_diff": float(home_rest - away_rest),
        }

    def _team_profile(self, history_df: pd.DataFrame, team_name: str, match_date: datetime) -> dict[str, float]:
        if history_df.empty:
            return self._default_profile(match_date)

        target_timestamp = pd.Timestamp(match_date)
        if target_timestamp.tzinfo is None:
            target_timestamp = target_timestamp.tz_localize("UTC")
        else:
            target_timestamp = target_timestamp.tz_convert("UTC")
        history_dates = pd.to_datetime(history_df["match_date"], utc=True, errors="coerce")
        eligible_history = history_df.loc[history_dates < target_timestamp]

        home_rows = self._build_team_rows(
            eligible_history.loc[eligible_history["home_team"] == team_name].copy(),
            side="home",
        )
        away_rows = self._build_team_rows(
            eligible_history.loc[eligible_history["away_team"] == team_name].copy(),
            side="away",
        )

        team_rows = pd.concat([home_rows, away_rows], ignore_index=True).sort_values("match_date")

        if team_rows.empty:
            return self._default_profile(match_date, history_df=history_df)

        recent_rows = team_rows.tail(5)
        rest_days = float((target_timestamp - team_rows["match_date"].max()).days)
        return {
            "matches_played": float(len(team_rows)),
            "goals_for_avg": float(team_rows["goals_for"].mean()),
            "goals_against_avg": float(team_rows["goals_against"].mean()),
            "xg_for_avg": float(team_rows["xg_for"].mean()),
            "xg_against_avg": float(team_rows["xg_against"].mean()),
            "points_per_match": float(team_rows["points"].mean()),
            "recent_points_per_match": float(recent_rows["points"].mean()),
            "win_rate": float(team_rows["win"].mean()),
            "recent_goals_for_avg": float(recent_rows["goals_for"].mean()),
            "recent_goals_against_avg": float(recent_rows["goals_against"].mean()),
            "recent_xg_for_avg": float(recent_rows["xg_for"].mean()),
            "recent_xg_against_avg": float(recent_rows["xg_against"].mean()),
            "rest_days": max(2.0, rest_days),
        }

    def _build_team_rows(self, side_rows: pd.DataFrame, *, side: str) -> pd.DataFrame:
        if side_rows.empty:
            return pd.DataFrame(columns=["match_date", "goals_for", "goals_against", "xg_for", "xg_against", "points", "win"])

        if side == "home":
            side_rows["goals_for"] = side_rows["home_goals"]
            side_rows["goals_against"] = side_rows["away_goals"]
            side_rows["xg_for"] = side_rows["home_xg"].fillna(side_rows["home_goals"])
            side_rows["xg_against"] = side_rows["away_xg"].fillna(side_rows["away_goals"])
            side_rows["points"] = np.select(
                [side_rows["home_goals"] > side_rows["away_goals"], side_rows["home_goals"] == side_rows["away_goals"]],
                [3, 1],
                default=0,
            )
            side_rows["win"] = (side_rows["home_goals"] > side_rows["away_goals"]).astype(float)
        else:
            side_rows["goals_for"] = side_rows["away_goals"]
            side_rows["goals_against"] = side_rows["home_goals"]
            side_rows["xg_for"] = side_rows["away_xg"].fillna(side_rows["away_goals"])
            side_rows["xg_against"] = side_rows["home_xg"].fillna(side_rows["home_goals"])
            side_rows["points"] = np.select(
                [side_rows["away_goals"] > side_rows["home_goals"], side_rows["away_goals"] == side_rows["home_goals"]],
                [3, 1],
                default=0,
            )
            side_rows["win"] = (side_rows["away_goals"] > side_rows["home_goals"]).astype(float)

        return side_rows[["match_date", "goals_for", "goals_against", "xg_for", "xg_against", "points", "win"]]

    def _default_profile(self, match_date: datetime, history_df: pd.DataFrame | None = None) -> dict[str, float]:
        default_goals_for = 1.2
        default_goals_against = 1.2
        default_points = 1.2
        if history_df is not None and not history_df.empty:
            goals_for = pd.concat([history_df["home_goals"], history_df["away_goals"]], ignore_index=True)
            xg_for = pd.concat(
                [
                    history_df["home_xg"].fillna(history_df["home_goals"]),
                    history_df["away_xg"].fillna(history_df["away_goals"]),
                ],
                ignore_index=True,
            )
            default_goals_for = float(goals_for.mean())
            default_goals_against = float(goals_for.mean())
            default_points = 1.25
            default_xg = float(xg_for.mean())
        else:
            default_xg = default_goals_for
        _ = match_date
        return {
            "matches_played": 0.0,
            "goals_for_avg": default_goals_for,
            "goals_against_avg": default_goals_against,
            "xg_for_avg": default_xg,
            "xg_against_avg": default_xg,
            "points_per_match": default_points,
            "recent_points_per_match": default_points,
            "win_rate": 0.33,
            "recent_goals_for_avg": default_goals_for,
            "recent_goals_against_avg": default_goals_against,
            "recent_xg_for_avg": default_xg,
            "recent_xg_against_avg": default_xg,
            "rest_days": 7.0,
        }

    def _normalize_frame(self, matches_df: pd.DataFrame) -> pd.DataFrame:
        if matches_df.empty:
            return pd.DataFrame(columns=["match_date", "home_team", "away_team", "home_goals", "away_goals", "competition_weight", "is_neutral_venue", "home_xg", "away_xg"])
        df = matches_df.copy()
        df["match_date"] = pd.to_datetime(df["match_date"], utc=True)
        df["home_xg"] = pd.to_numeric(df.get("home_xg"), errors="coerce")
        df["away_xg"] = pd.to_numeric(df.get("away_xg"), errors="coerce")
        return df.sort_values("match_date").reset_index(drop=True)

    @staticmethod
    def _format_timestamp(value: object) -> str | None:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return None
        timestamp = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.isna(timestamp):
            return None
        return timestamp.isoformat()
