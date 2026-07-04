"""StackingMetaLearner — Logistic Regression stacking on all 7 component probs.

Follows the ``fit()`` / ``predict()`` / ``save()`` / ``load()`` service
interface pattern established by ``IsotonicCalibrator`` (calibration.py).

Serialises model coefficients as JSON (no pickle) for portability and
auditability.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.core.stacking_features import (
    assemble_feature_vector,
    STACKING_C,
    STACKING_MAX_ITER,
    STACKING_MIN_TRAINING_SAMPLES,
    STACKING_FEATURE_KEYS,
)

logger = logging.getLogger(__name__)

REQUIRED_CLASSES = (0, 1, 2)
EXPECTED_FEATURE_COUNT = len(STACKING_FEATURE_KEYS) * 3


class StackingMetaLearner:
    """Multinomial logistic regression stacking meta-learner.

    Takes all 7 component probabilities (21 features: 3 outcomes × 7
    components) and outputs calibrated H/D/A probabilities.
    """

    def __init__(self) -> None:
        self._coef: list[list[float]] = []     # shape (3, 21)
        self._intercept: list[float] = []       # shape (3,)
        self._classes: list[int] = [0, 1, 2]    # H, D, A
        self.is_fitted: bool = False
        self.fitted_at: datetime | None = None
        self.training_sample_count: int = 0
        self.feature_names: tuple[str, ...] = STACKING_FEATURE_KEYS

    # ── Fit ──────────────────────────────────────────────────────────

    def fit(
        self,
        X: list[list[float]] | np.ndarray,
        y: list[int] | np.ndarray,
    ) -> "StackingMetaLearner":
        """Fit multinomial logistic regression.

        Args:
            X: Feature matrix of shape (n_samples, 21).
            y: Target labels in {0, 1, 2} of shape (n_samples,).

        Returns:
            ``self`` for chaining.
        """
        X_np = np.asarray(X, dtype=float)
        y_np = np.asarray(y, dtype=int)

        n_samples = X_np.shape[0]
        if X_np.ndim != 2 or X_np.shape[1] != EXPECTED_FEATURE_COUNT:
            logger.info(
                "StackingMetaLearner.fit: invalid feature shape %s; expected (*, %d)",
                X_np.shape,
                EXPECTED_FEATURE_COUNT,
            )
            self.is_fitted = False
            return self

        if n_samples < STACKING_MIN_TRAINING_SAMPLES:
            logger.info(
                "StackingMetaLearner.fit: %d samples < %d minimum — not fitting",
                n_samples, STACKING_MIN_TRAINING_SAMPLES,
            )
            self.is_fitted = False
            return self
        observed_classes = tuple(sorted(int(item) for item in set(y_np.tolist())))
        if observed_classes != REQUIRED_CLASSES:
            logger.info(
                "StackingMetaLearner.fit: classes %s do not cover required %s",
                observed_classes,
                REQUIRED_CLASSES,
            )
            self.is_fitted = False
            return self

        try:
            from sklearn.linear_model import LogisticRegression
        except Exception as exc:
            logger.warning("sklearn not available, stacking unfitted: %s", exc)
            self.is_fitted = False
            return self

        try:
            model = LogisticRegression(
                multi_class="multinomial", solver="lbfgs",
                C=STACKING_C, max_iter=STACKING_MAX_ITER, random_state=42,
            )
        except TypeError:
            model = LogisticRegression(
                solver="lbfgs", C=STACKING_C, max_iter=STACKING_MAX_ITER, random_state=42,
            )
        model.fit(X_np, y_np)

        self._coef = model.coef_.tolist()          # (3, 21)
        self._intercept = model.intercept_.tolist()  # (3,)
        self._classes = [int(c) for c in model.classes_]
        self.is_fitted = True
        if not self._is_ready():
            self.is_fitted = False
            logger.warning(
                "StackingMetaLearner.fit produced invalid artifact shape/classes; unfitted"
            )
            return self
        self.fitted_at = datetime.now(timezone.utc)
        self.training_sample_count = n_samples

        logger.info(
            "StackingMetaLearner fitted on %d samples, coef shape=%s",
            n_samples, model.coef_.shape,
        )
        return self

    def fit_from_records(
        self,
        X: list[list[float]] | np.ndarray,
        y: list[int] | np.ndarray,
    ) -> "StackingMetaLearner":
        """Alias for ``fit()`` with a discoverable name matching the
        calibrator pattern."""
        return self.fit(X, y)

    # ── Predict ──────────────────────────────────────────────────────

    def predict_proba(
        self,
        component_probs: dict[str, dict[str, float]],
        market_probs: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """Predict calibrated H/D/A probabilities.

        Args:
            component_probs: Dict of component → {home, draw, away}.
            market_probs: Optional separate market-implied probs.

        Returns:
            ``{"home_win_prob": float, "draw_prob": float,
              "away_win_prob": float}``.  Falls back to uniform (⅓ each)
            when the model is not fitted.
        """
        if not self._is_ready():
            return self._uniform_probs()

        X = np.asarray(
            [assemble_feature_vector(component_probs, market_probs)],
            dtype=float,
        )  # (1, 21)

        # Manual softmax: P(c) ∝ exp(coef_c · x + intercept_c)
        logits = np.dot(X, np.asarray(self._coef).T) + np.asarray(self._intercept)
        logits = logits[0]  # (3,)

        # Numerically stable softmax
        logits_max = np.max(logits)
        exps = np.exp(logits - logits_max)
        probs_arr = exps / exps.sum()

        # Map class index → outcome key
        idx_to_key = {
            0: "home_win_prob",
            1: "draw_prob",
            2: "away_win_prob",
        }
        result: dict[str, float] = {
            "home_win_prob": 0.0,
            "draw_prob": 0.0,
            "away_win_prob": 0.0,
        }
        for i, cls in enumerate(self._classes):
            key = idx_to_key.get(int(cls), f"class_{cls}")
            result[key] = float(probs_arr[i])

        total = sum(result[key] for key in idx_to_key.values())
        if total <= 0:
            return self._uniform_probs()
        return {key: result[key] / total for key in idx_to_key.values()}

    # ── Serialization ────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """Save model coefficients as JSON."""
        payload: dict[str, Any] = {
            "coef": self._coef,
            "intercept": self._intercept,
            "classes": self._classes,
            "feature_names": list(self.feature_names),
            "is_fitted": self.is_fitted,
            "fitted_at": self.fitted_at.isoformat() if self.fitted_at else None,
            "training_sample_count": self.training_sample_count,
            "C": STACKING_C,
            "max_iter": STACKING_MAX_ITER,
        }
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, path: str) -> None:
        """Load model coefficients from JSON."""
        target = Path(path)
        if not target.exists():
            self.__init__()
            return

        payload = json.loads(target.read_text(encoding="utf-8"))
        self._coef = payload.get("coef", [])
        self._intercept = payload.get("intercept", [])
        self._classes = payload.get("classes", [0, 1, 2])
        self.is_fitted = bool(payload.get("is_fitted", False))
        fitted_at = payload.get("fitted_at")
        self.fitted_at = datetime.fromisoformat(fitted_at) if fitted_at else None
        self.training_sample_count = int(payload.get("training_sample_count", 0))
        self.feature_names = tuple(payload.get("feature_names", STACKING_FEATURE_KEYS))
        if not self._is_ready():
            self.is_fitted = False

    # ── Diagnostics ──────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return training metadata for pipeline logging."""
        return {
            "is_fitted": self.is_fitted,
            "training_samples": self.training_sample_count,
            "fitted_at": self.fitted_at.isoformat() if self.fitted_at else None,
            "n_features": len(self.feature_names) * 3,
            "feature_names": list(self.feature_names),
        }

    @staticmethod
    def _uniform_probs() -> dict[str, float]:
        return {
            "home_win_prob": 1.0 / 3.0,
            "draw_prob": 1.0 / 3.0,
            "away_win_prob": 1.0 / 3.0,
        }

    def _is_ready(self) -> bool:
        if not self.is_fitted:
            return False
        if tuple(int(item) for item in self._classes) != REQUIRED_CLASSES:
            return False
        try:
            coef = np.asarray(self._coef, dtype=float)
            intercept = np.asarray(self._intercept, dtype=float)
        except (TypeError, ValueError):
            return False
        return (
            coef.shape == (len(REQUIRED_CLASSES), EXPECTED_FEATURE_COUNT)
            and intercept.shape == (len(REQUIRED_CLASSES),)
        )
