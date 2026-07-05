"""ko_draw_guard.py — Post-Calibration Knockout Draw Guard.

Warns when a knockout-stage prediction has an implausibly low draw
probability after calibration.  This is a *diagnostic* guard (Phase 1:
warn-only) — it does NOT alter probabilities.  A Bayesian offset model
may be added in Phase 2 after accumulating enough KO samples.

Background
----------
The current pipeline executes draw-floor enforcement *before* Isotonic
calibration:

    Step 13.4: enforce_draw_floor(probs)  → draw >= 12%
    Step 13.5: calibrator.calibrate(probs) → may suppress draw again

If the calibrator learned from historical data where knockout draws were
under-represented, it can re-suppress the draw probability.  This was
observed in NED-MAR and GER-PAR post-match reviews.

Trigger conditions
------------------
All of the following must be true:

1. ``is_knockout == True``
2. ``draw_prob < 0.22``
3. At least one risk factor:
   - ``|elo_gap| < 50`` (teams are closely matched)
   - ``total_xg < 2.35`` (low-scoring match expected)
   - ``market_draw >= 0.25`` (market disagrees with model)
   - ``model_disagreement == True`` (components diverge)

Usage::

    from app.core.ko_draw_guard import check_ko_draw_guard

    guard_result = check_ko_draw_guard(
        draw_prob=0.18,
        is_knockout=True,
        elo_gap=30,
        total_xg=2.1,
        market_draw_prob=0.26,
    )
    if guard_result["triggered"]:
        logger.warning("KO draw guard triggered: %s", guard_result["reason"])
"""
from __future__ import annotations

from typing import Any

# ── Feature flag ──
KO_DRAW_GUARD_ENABLED = True

# ── Thresholds ──
KO_DRAW_FLOOR_WARNING = 0.20       # draw below this triggers review (0.22→0.20: AUS-EGY missed by 0.85pp)
ELO_GAP_CLOSE_THRESHOLD = 50       # |gap| below this is "close match"
TOTAL_XG_LOW_THRESHOLD = 2.35      # total xG below this is "low scoring"
MARKET_DRAW_DISAGREEMENT = 0.25    # market draw >= this indicates disagreement

# ── Post-calibration correction (Phase 2) ──
POST_CAL_KO_DRAW_GUARD_ENABLED = True  # Feature flag for Phase 2 correction
MAX_POST_CAL_BLEND = 0.80              # Max blend toward pre-calibration draw (0.60→0.80: more aggressive)
POST_CAL_BLEND_RISK_WEIGHT = 0.15      # Blend per risk factor present (0.12→0.15)

# Knockout stage names (case-insensitive prefix match)
KO_STAGE_PREFIXES = (
    "round of 32", "round of 16", "round of 8",
    "quarter-final", "quarterfinal",
    "semi-final", "semifinal",
    "final", "third place",
)


def _is_ko_stage(stage: str | None) -> bool:
    """Return True if *stage* looks like a knockout round."""
    if not stage:
        return False
    stage_lower = stage.strip().lower()
    return any(stage_lower.startswith(prefix) for prefix in KO_STAGE_PREFIXES)


def check_ko_draw_guard(
    *,
    draw_prob: float,
    is_knockout: bool = False,
    stage: str | None = None,
    elo_gap: float | None = None,
    total_xg: float | None = None,
    market_draw_prob: float | None = None,
    model_disagreement: bool = False,
) -> dict[str, Any]:
    """Check whether a knockout prediction may be underestimating the draw.

    Returns a dict with keys:
        - ``checked``: bool (always True if guard ran)
        - ``triggered``: bool
        - ``reason``: str (empty if not triggered)
        - ``risk_factors``: list[str]
        - ``action``: str — always ``"warn_only"`` in Phase 1
    """
    if not KO_DRAW_GUARD_ENABLED:
        return {
            "checked": False,
            "triggered": False,
            "reason": "guard disabled",
            "risk_factors": [],
            "action": "none",
        }

    # Resolve knockout status from stage name if not explicitly set
    if not is_knockout and stage:
        is_knockout = _is_ko_stage(stage)

    if not is_knockout:
        return {
            "checked": True,
            "triggered": False,
            "reason": "not a knockout match",
            "risk_factors": [],
            "action": "none",
        }

    if draw_prob >= KO_DRAW_FLOOR_WARNING:
        return {
            "checked": True,
            "triggered": False,
            "reason": f"draw_prob ({draw_prob:.3f}) >= floor ({KO_DRAW_FLOOR_WARNING})",
            "risk_factors": [],
            "action": "none",
        }

    # ── Evaluate risk factors ──
    risk_factors: list[str] = []

    if elo_gap is not None and abs(elo_gap) < ELO_GAP_CLOSE_THRESHOLD:
        risk_factors.append(
            f"close Elo gap ({abs(elo_gap):.0f} < {ELO_GAP_CLOSE_THRESHOLD})"
        )

    if total_xg is not None and total_xg < TOTAL_XG_LOW_THRESHOLD:
        risk_factors.append(
            f"low total xG ({total_xg:.2f} < {TOTAL_XG_LOW_THRESHOLD})"
        )

    if market_draw_prob is not None and market_draw_prob >= MARKET_DRAW_DISAGREEMENT:
        risk_factors.append(
            f"market draw higher ({market_draw_prob:.3f} >= {MARKET_DRAW_DISAGREEMENT})"
        )

    if model_disagreement:
        risk_factors.append("high model disagreement")

    if not risk_factors:
        return {
            "checked": True,
            "triggered": False,
            "reason": (
                f"draw_prob ({draw_prob:.3f}) < floor but no risk factors present"
            ),
            "risk_factors": [],
            "action": "none",
        }

    return {
        "checked": True,
        "triggered": True,
        "reason": (
            f"KO draw {draw_prob:.1%} below {KO_DRAW_FLOOR_WARNING:.0%} with "
            f"risk factors: {', '.join(risk_factors)}"
        ),
        "risk_factors": risk_factors,
        "action": "warn_only",
    }


# ── Phase 2: Post-calibration correction ──────────────────────────────

def _safe_get_prob(probs: dict[str, float], *keys: str) -> float:
    """Extract a probability from a dict, trying multiple key names."""
    for k in keys:
        v = probs.get(k)
        if v is not None:
            return float(v)
    return 0.0


def _normalize_triplet(
    home: float, draw: float, away: float,
) -> tuple[float, float, float]:
    """Normalize three probabilities to sum to 1.0."""
    total = home + draw + away
    if total <= 0:
        return (1 / 3, 1 / 3, 1 / 3)
    return (home / total, draw / total, away / total)


def enforce_ko_draw_post_calibration(
    pre_calibration_probs: dict[str, float],
    post_calibration_probs: dict[str, float],
    *,
    is_knockout: bool = False,
    stage: str | None = None,
    elo_gap: float | None = None,
    total_xg: float | None = None,
    market_draw_prob: float | None = None,
    model_disagreement: bool = False,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Post-calibration KO draw guard — Phase 2 correction.

    When Isotonic calibration suppresses KO draw probability below the
    warning threshold, blend back toward the pre-calibration value.

    The blend ratio increases with:
      - The magnitude of calibration suppression (pre_draw - post_draw)
      - The number of risk factors present

    Returns:
        (adjusted_probs, guard_result) — probs unchanged if not triggered.
    """
    if not POST_CAL_KO_DRAW_GUARD_ENABLED:
        return dict(post_calibration_probs), {
            "checked": False, "triggered": False,
            "reason": "post-cal guard disabled", "action": "none",
        }

    # Resolve knockout status
    if not is_knockout and stage:
        is_knockout = _is_ko_stage(stage)
    if not is_knockout:
        return dict(post_calibration_probs), {
            "checked": True, "triggered": False,
            "reason": "not a knockout match", "action": "none",
        }

    # Extract probabilities (handle both key naming conventions)
    pre_draw = _safe_get_prob(pre_calibration_probs, "draw_prob", "draw")
    post_draw = _safe_get_prob(post_calibration_probs, "draw_prob", "draw")
    post_home = _safe_get_prob(post_calibration_probs, "home_win_prob", "home")
    post_away = _safe_get_prob(post_calibration_probs, "away_win_prob", "away")

    # Only trigger if calibration *reduced* the draw below threshold
    if post_draw >= KO_DRAW_FLOOR_WARNING:
        return dict(post_calibration_probs), {
            "checked": True, "triggered": False,
            "reason": f"post-cal draw ({post_draw:.3f}) >= {KO_DRAW_FLOOR_WARNING}",
            "action": "none",
        }

    if post_draw >= pre_draw - 0.005:
        # Calibration didn't meaningfully suppress draw — guard not needed
        return dict(post_calibration_probs), {
            "checked": True, "triggered": False,
            "reason": f"calibration did not suppress draw (pre={pre_draw:.3f} post={post_draw:.3f})",
            "action": "none",
        }

    # ── Evaluate risk factors (same as Phase 1) ──
    risk_factors: list[str] = []
    if elo_gap is not None and abs(elo_gap) < ELO_GAP_CLOSE_THRESHOLD:
        risk_factors.append(f"close Elo gap ({abs(elo_gap):.0f})")
    if total_xg is not None and total_xg < TOTAL_XG_LOW_THRESHOLD:
        risk_factors.append(f"low total xG ({total_xg:.2f})")
    if market_draw_prob is not None and market_draw_prob >= MARKET_DRAW_DISAGREEMENT:
        risk_factors.append(f"market draw higher ({market_draw_prob:.3f})")
    if model_disagreement:
        risk_factors.append("high model disagreement")

    if not risk_factors:
        return dict(post_calibration_probs), {
            "checked": True, "triggered": False,
            "reason": "no risk factors present",
            "action": "none",
        }

    # ── Compute blend ratio ──
    # suppression_pp: how much calibration reduced the draw
    suppression_pp = max(0.0, pre_draw - post_draw)
    # deficit_pp: how far below the warning threshold
    deficit_pp = KO_DRAW_FLOOR_WARNING - post_draw

    # Base blend from suppression significance
    base_blend = min(1.0, suppression_pp / max(deficit_pp, 0.01))
    # Add risk factor weight
    risk_blend = min(len(risk_factors) * POST_CAL_BLEND_RISK_WEIGHT, 0.40)
    # Final blend capped at MAX_POST_CAL_BLEND
    blend_ratio = min(base_blend * 0.50 + risk_blend, MAX_POST_CAL_BLEND)

    # ── Apply blend ──
    corrected_draw = post_draw * (1.0 - blend_ratio) + pre_draw * blend_ratio
    # Clamp to [0.15, 0.22] — don't exceed the warning threshold significantly
    corrected_draw = max(0.15, min(corrected_draw, KO_DRAW_FLOOR_WARNING + 0.03))

    draw_increase = corrected_draw - post_draw

    # Take deficit 70% from favorite, 30% from underdog
    if post_home >= post_away:
        corrected_home = max(0.02, post_home - draw_increase * 0.70)
        corrected_away = max(0.02, post_away - draw_increase * 0.30)
    else:
        corrected_home = max(0.02, post_home - draw_increase * 0.30)
        corrected_away = max(0.02, post_away - draw_increase * 0.70)

    h, d, a = _normalize_triplet(corrected_home, corrected_draw, corrected_away)

    adjusted = {
        "home_win_prob": h,
        "draw_prob": d,
        "away_win_prob": a,
    }

    return adjusted, {
        "checked": True,
        "triggered": True,
        "reason": (
            f"KO post-cal draw guard: {post_draw:.1%} → {d:.1%} "
            f"(blend={blend_ratio:.0%}, risks: {', '.join(risk_factors)})"
        ),
        "risk_factors": risk_factors,
        "action": "blend_correction",
        "blend_ratio": round(blend_ratio, 4),
        "pre_calibration_draw": round(pre_draw, 4),
        "post_calibration_draw": round(post_draw, 4),
        "corrected_draw": round(d, 4),
    }
