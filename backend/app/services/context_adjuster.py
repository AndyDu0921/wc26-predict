"""ContextAdjuster — applies context-based adjustments from learned history.

Reads context_performance_matrix to find systematic biases for
specific match contexts (derby, must_win, relegation_battle, etc.)
and applies micro-corrections to the fused probabilities.

Adjustments are very small (max ±3pp) and only activate when
≥10 matches of data exist for that context.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

MIN_SAMPLES = 10
MIN_CONFIDENCE = 0.3
MAX_ADJUSTMENT = 0.03  # Max ±3pp per context


class ContextAdjuster:
    """Apply learned context-based probability adjustments."""

    async def apply_context_adjustments(
        self,
        probs: dict[str, float],
        context_tags: list[str],
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Adjust probabilities based on historical context performance.

        Returns probs dict with optional context_adjustments field.
        Does nothing if no contexts have sufficient data.
        """
        if not context_tags:
            return {**probs, "context_adjustments": []}

        total_home_adj = 0.0
        total_draw_adj = 0.0
        applied = []

        for tag in context_tags:
            try:
                result = await db.execute(
                    text(
                        "SELECT recommended_adjustment, 0.0 AS recommended_draw_adjustment, "
                        "COALESCE(1.0 - avg_brier_score, 0.5) AS confidence, "
                        "total_matches FROM context_performance_matrix "
                        "WHERE context_tag = :tag AND total_matches >= :min_n"
                    ),
                    {"tag": tag, "min_n": MIN_SAMPLES},
                )
                row = result.fetchone()
                if not row:
                    continue

                home_adj, draw_adj, confidence, n = (
                    float(row[0] or 0),
                    float(row[1] or 0),
                    float(row[2] or 0),
                    int(row[3] or 0),
                )

                if confidence < MIN_CONFIDENCE:
                    continue

                # Scale by confidence (low confidence → small adjustment)
                effective_home = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, home_adj * confidence))
                effective_draw = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, draw_adj * confidence))

                total_home_adj += effective_home
                total_draw_adj += effective_draw

                applied.append({
                    "tag": tag,
                    "home_adjustment": effective_home,
                    "draw_adjustment": effective_draw,
                    "confidence": confidence,
                    "based_on_matches": n,
                })
            except Exception as e:
                logger.debug(f"ContextAdjuster skip {tag}: {e}")
                continue

        if not applied:
            return {**probs, "context_adjustments": []}

        # Apply adjustments
        new_home = max(0.02, probs["home_win_prob"] + total_home_adj)
        new_draw = max(0.02, probs["draw_prob"] + total_draw_adj)
        new_away = max(0.02, probs["away_win_prob"] - total_home_adj - total_draw_adj)

        total = new_home + new_draw + new_away
        if total > 0:
            new_home /= total
            new_draw /= total
            new_away /= total

        return {
            "home_win_prob": new_home,
            "draw_prob": new_draw,
            "away_win_prob": new_away,
            "context_adjustments": applied,
        }


_context_adjuster: ContextAdjuster | None = None


def get_context_adjuster() -> ContextAdjuster:
    global _context_adjuster
    if _context_adjuster is None:
        _context_adjuster = ContextAdjuster()
    return _context_adjuster
