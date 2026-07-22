"""Bivariate Weibull Count Model via penaltyblog.

The Weibull model assumes goal inter-arrival times follow a Weibull
distribution (not exponential/Poisson), capturing momentum effects.
A Frank Copula introduces inter-team correlation, making it better
than independent Poisson for over/under 2.5 goal predictions.

Reference: Boshnakov, Kharrat & McHale (2017).

Integration: Optional complement to Dixon-Coles, weighted ~15% in UCL scenes.
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── Session-level model cache ──
# penaltyblog WeibullCopulaGoalsModel.fit() takes ~69s for 500 rows (16706 total).
# Re-fitting per match is wasteful → cache the fitted model at module level so
# predict_match_full.py, prediction_pipeline.py, and tournament simulators all
# reuse the same instance within a single process.
_WEIBULL_CACHE: dict[str, Any] = {}
_WEIBULL_CACHE_LOCK = __import__("threading").Lock()  # guards concurrent access


class WeibullWrapper:
    """Wrapper around penaltyblog WeibullCopulaGoalsModel.

    Session-level caching: the first call to fit() trains the model; subsequent
    calls across the same process reuse the cached instance (keyed by df hash).
    """

    # ── Performance constants (benchmarked 2026-06-23) ──
    # Rows | Fit time
    #   100 |   2.3s
    #   200 |  13.2s
    #   500 |  68.8s
    #  1000 | ~138s  (extrapolated)
    # We cap at 500 rows with a 2-minute timeout.
    MAX_ROWS = 500
    FIT_TIMEOUT = 120.0

    def __init__(self):
        self._model: Any | None = None
        self._fitted: bool = False
        self._fit_thread: object | None = None  # Track active fit thread

    @staticmethod
    def _df_key(df: pd.DataFrame) -> str:
        """Stable hash keyed on row count, max match_date, and sorted unique team names.

        Includes BOTH home and away teams to prevent collisions between DFs that
        happen to share home-team sets but differ on away opponents (e.g. the
        same home side playing different visiting sides).
        """
        import hashlib

        home_unique = ",".join(sorted(df["home_team"].unique()))
        away_unique = ",".join(sorted(df["away_team"].unique()))
        payload = f"{len(df)}|{df['match_date'].max()}|{home_unique}|{away_unique}"
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def fit(self, df: pd.DataFrame) -> bool:
        """Fit the Weibull model on training data.

        Returns True if fitting succeeded, False otherwise.

        Uses a thread-based timeout and limits training data to the most recent
        ``MAX_ROWS`` rows.  The fitted model is cached at module level so that
        multiple prediction calls within the same process share a single fit.

        Thread safety: if a previous fit is still running, it is abandoned and
        a new thread is started. The daemon threads will terminate when the
        process exits, but they cannot be interrupted mid-fit (penaltyblog does
        not support cancellation). This is acceptable for a best-effort model
        component with an existing 30% error rate.
        """
        import threading

        # ── Session-level cache check (thread-safe) ──
        key = self._df_key(df)
        with _WEIBULL_CACHE_LOCK:
            cached = _WEIBULL_CACHE.get(key)
        if cached is not None:
            self._model = cached["model"]
            self._fitted = cached["fitted"]
            logger.info("Weibull: using cached model (key=%s)", key)
            return self._fitted

        # ── Abandon previous fit thread if still running ──
        if self._fit_thread is not None and getattr(self._fit_thread, "is_alive", lambda: False)():
            logger.warning(
                "Weibull: abandoning previous fit thread (still running after timeout)"
            )
        self._fit_thread = None

        # Trim to most recent rows
        if len(df) > self.MAX_ROWS:
            df = df.sort_values("match_date", ascending=False).head(self.MAX_ROWS)

        result_ok = [False]
        result_model: list[Any] = [None]
        result_exc: list[Exception] = [None]

        def _fit():
            try:
                from penaltyblog.models import WeibullCopulaGoalsModel

                # Use np.array(..., copy=True) to avoid "buffer source array
                # is read-only" error from penaltyblog internals.
                weights_col = df.get("competition_weight", None)
                wc = WeibullCopulaGoalsModel(
                    goals_home=np.array(df["home_goals"].values, copy=True),
                    goals_away=np.array(df["away_goals"].values, copy=True),
                    teams_home=np.array(df["home_team"].values, copy=True),
                    teams_away=np.array(df["away_team"].values, copy=True),
                    weights=(
                        np.array(weights_col.values, copy=True)
                        if weights_col is not None
                        else None
                    ),
                )
                wc.fit()
                result_model[0] = wc
                result_ok[0] = True
                logger.info("Weibull model fitted successfully (%d rows)", len(df))
            except Exception as e:
                result_exc[0] = e
                logger.warning("Weibull model fit failed: %s", e)

        t = threading.Thread(target=_fit, daemon=True, name=f"weibull-fit-{key}")
        self._fit_thread = t
        t.start()
        t.join(timeout=self.FIT_TIMEOUT)

        if t.is_alive():
            # Thread did not complete within timeout — penaltyblog is still
            # running.  Daemon thread will be abandoned (cleaned up at process
            # exit, not before).
            logger.warning(
                "Weibull model fit timed out after %.0fs (%d rows) — "
                "thread %s abandoned (daemon=true, will exit with process)",
                self.FIT_TIMEOUT,
                len(df),
                t.name,
            )
            self._fitted = False
            self._fit_thread = None  # clear so is_fitting() doesn't report stale state
            with _WEIBULL_CACHE_LOCK:
                _WEIBULL_CACHE[key] = {"model": None, "fitted": False}
            return False

        self._fit_thread = None
        if result_ok[0]:
            self._model = result_model[0]
            self._fitted = True
            with _WEIBULL_CACHE_LOCK:
                _WEIBULL_CACHE[key] = {"model": result_model[0], "fitted": True}
            return True

        if result_exc[0]:
            logger.debug(
                "Weibull fit exception detail: %s", result_exc[0], exc_info=result_exc[0]
            )
        with _WEIBULL_CACHE_LOCK:
            _WEIBULL_CACHE[key] = {"model": None, "fitted": False}
        return False

    def predict(
        self, home_team: str, away_team: str, neutral: bool = True
    ) -> dict[str, float] | None:
        """Predict win/draw/loss probabilities using fitted Weibull model.

        Note: penaltyblog uses ``neutral_venue=`` keyword, **not** ``neutral=``.
        """
        if not self._fitted or self._model is None:
            return None
        try:
            grid = self._model.predict(
                home_team, away_team, neutral_venue=neutral
            )
            return {
                "home_win_prob": float(grid.home_win),
                "draw_prob": float(grid.draw),
                "away_win_prob": float(grid.away_win),
            }
        except Exception as e:
            logger.warning("Weibull predict failed: %s", e)
            return None

    def predict_score_matrix(
        self, home_team: str, away_team: str, neutral: bool = True, max_goals: int = 5,
    ) -> list[list[float]] | None:
        """Extract the full score probability matrix from the Weibull Copula model.

        The Weibull count + Frank Copula model (Boshnakov et al. 2017, IJF)
        generates a bivariate joint distribution over home/away goals.  This
        method extracts the full matrix, not just the H/D/A aggregates.

        Parameters
        ----------
        home_team:
            Home team name.
        away_team:
            Away team name.
        neutral:
            If True, home advantage is excluded.
        max_goals:
            Maximum goals per side in returned matrix.  The penaltyblog
            model internally computes up to 15 goals; we truncate to
            ``max_goals`` and re-normalise.

        Returns
        -------
        list[list[float]] or None
            (max_goals+1)×(max_goals+1) matrix, sum ≈ 1.0, or None on failure.
        """
        if not self._fitted or self._model is None:
            return None
        try:
            grid = self._model.predict(
                home_team, away_team, neutral_venue=neutral, max_goals=max_goals,
            )
            raw = grid.grid  # numpy 2-D array, shape (max_goals+1, max_goals+1)
            # Convert to list-of-lists
            matrix = raw.tolist()
            # Re-normalise after truncation
            total = sum(sum(row) for row in matrix)
            if total > 0:
                for h in range(len(matrix)):
                    for a in range(len(matrix[0])):
                        matrix[h][a] /= total
            return matrix
        except Exception as e:
            logger.warning("Weibull score matrix failed: %s", e)
            return None

    @property
    def is_fitting(self) -> bool:
        """True if a background fit thread is still running."""
        t = self._fit_thread
        return t is not None and getattr(t, "is_alive", lambda: False)()


def fuse_weibull_probs(base_probs: dict, wb_pred: dict | None, wb_weight: float = 0.15) -> dict:
    """Blend Weibull probabilities into the ensemble.

    If Weibull failed to fit/predict, returns base_probs unchanged.
    """
    if wb_pred is None:
        return dict(base_probs)

    fused = {}
    for key in ("home_win_prob", "draw_prob", "away_win_prob"):
        fused[key] = base_probs[key] * (1.0 - wb_weight) + wb_pred[key] * wb_weight
    total = fused["home_win_prob"] + fused["draw_prob"] + fused["away_win_prob"]
    for key in fused:
        fused[key] /= total

    return fused
