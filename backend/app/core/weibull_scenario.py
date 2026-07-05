"""weibull_scenario.py — Weibull Scenario Rules (P1-2 Phase 2).

Replaces the simple binary skip logic for Weibull extreme probabilities with
a 4-action decision framework that considers match context (Elo gap, market
agreement, xG expectation, knockout stage) before deciding what to do with
Weibull's output.

Background
----------
Weibull's Bivariate Poisson tail model produces extreme probabilities in
certain matchups — e.g. 78.2% home win, 64.8% draw, 46.8% away win. The old
approach of treating >70% as an automatic skip is too coarse. This module
classifies Weibull output into one of four scenarios and prescribes a
context-aware action:

Action 1 — ``"keep"`` (强队碾压极端值):
    Weibull extreme home/away probability backed by large Elo gap, market
    agreement, and high xG — the model correctly identifies a mismatch.

Action 2 — ``"shadow"`` (平局极端噪声 → V4.8.1: downgraded from skip):
    Weibull extreme draw probability in a knockout match without fundamental
    support — preserve at reduced weight (KO draw rate 25% makes this
    pattern diagnostically valuable).

Action 3 — ``"shadow"`` (与市场冲突):
    Weibull extreme conflicts with tight market consensus — apply but flag
    for post-match review, reduce weight by 50%.

Action 4 — ``"normal"`` (正常融合):
    Weibull probabilities within normal bounds — normal fusion at full weight.

Usage::

    from app.core.weibull_scenario import classify_weibull_scenario, resolve_weibull_action

    scenario = classify_weibull_scenario(
        weibull_probs={"home_win_prob": 0.46, "draw_prob": 0.65, "away_win_prob": 0.04},
        elo_gap=42,
        is_knockout=True,
        market_probs={"home_prob": 0.45, "draw_prob": 0.25, "away_prob": 0.30},
    )
    action = resolve_weibull_action(scenario, weibull_weight=0.10)
    # → {"action": "skip", "effective_weight": 0.0, "reason": "..."}
"""
from __future__ import annotations

from typing import Any, Literal

# ── Feature flag ──
WEIBULL_SCENARIO_ENABLED = True

# ── Thresholds ──
WEIBULL_EXTREME_HOME = 0.60       # home >= this is "extreme home"
WEIBULL_EXTREME_AWAY = 0.60       # away >= this is "extreme away"
WEIBULL_EXTREME_DRAW = 0.40       # draw >= this is "extreme draw" (KO baseline ~22%)
WEIBULL_DOMINATION_ELO_GAP = 80   # |Elo gap| >= this suggests genuine mismatch
WEIBULL_DOMINATION_MARKET_AGREE = 0.10  # market favorite must be >= 10pp ahead
WEIBULL_SHADOW_WEIGHT_REDUCTION = 0.50  # 50% weight reduction in shadow mode
WEIBULL_MAX_SCENARIO_WEIGHT = 0.05      # max weight in shadow mode

ScenarioName = Literal["domination", "draw_noise", "market_conflict", "normal"]
ActionName = Literal["keep", "skip", "shadow", "normal"]


def classify_weibull_scenario(
    weibull_probs: dict[str, float],
    *,
    elo_gap: float | None = None,
    is_knockout: bool = False,
    market_probs: dict[str, float] | None = None,
    total_xg: float | None = None,
) -> dict[str, Any]:
    """Classify the Weibull probability pattern into a scenario.

    Returns a dict with keys:
        - ``scenario``: one of "domination", "draw_noise", "market_conflict", "normal"
        - ``reason``: human-readable explanation
        - ``features``: dict of extracted signals for downstream logging
    """
    if not WEIBULL_SCENARIO_ENABLED:
        return {"scenario": "normal", "reason": "scenario rules disabled",
                "features": {}}

    wh = float(weibull_probs.get("home_win_prob", 0.33))
    wd = float(weibull_probs.get("draw_prob", 0.33))
    wa = float(weibull_probs.get("away_win_prob", 0.34))

    features: dict[str, Any] = {
        "weibull_home": round(wh, 4),
        "weibull_draw": round(wd, 4),
        "weibull_away": round(wa, 4),
        "elo_gap": round(elo_gap, 0) if elo_gap is not None else None,
        "is_knockout": is_knockout,
    }

    # ── Rule 1: Extreme draw in knockout → draw_noise ──
    if wd >= WEIBULL_EXTREME_DRAW and is_knockout:
        features["trigger"] = f"draw={wd:.1%} >= {WEIBULL_EXTREME_DRAW:.0%} in KO"
        # Check if market also sees high draw (mitigating factor)
        if market_probs:
            md = float(market_probs.get("draw_prob", 0.25))
            features["market_draw"] = round(md, 4)
            if md >= 0.28:
                # Market also prices high draw — downgrade to shadow, not skip
                return {
                    "scenario": "market_conflict",
                    "reason": (
                        f"Weibull extreme draw ({wd:.1%}) in KO but market "
                        f"also high ({md:.1%}) — shadow mode"
                    ),
                    "features": features,
                }
        return {
            "scenario": "draw_noise",
            "reason": (
                f"Weibull extreme draw ({wd:.1%}) in KO match "
                f"(baseline ~22%) — likely noise, discard"
            ),
            "features": features,
        }

    # ── Rule 2: Extreme home/away with fundamental support → domination ──
    fav_extreme = wh >= WEIBULL_EXTREME_HOME or wa >= WEIBULL_EXTREME_AWAY
    if fav_extreme:
        abs_elo = abs(elo_gap) if elo_gap is not None else 0
        if abs_elo >= WEIBULL_DOMINATION_ELO_GAP:
            # Check market agreement
            market_agrees = False
            if market_probs:
                mh = float(market_probs.get("home_prob", 0.33))
                ma = float(market_probs.get("away_prob", 0.33))
                if wh > wa and mh > ma + WEIBULL_DOMINATION_MARKET_AGREE:
                    market_agrees = True
                elif wa > wh and ma > mh + WEIBULL_DOMINATION_MARKET_AGREE:
                    market_agrees = True

            if market_agrees:
                features["trigger"] = (
                    f"extreme={'home' if wh > wa else 'away'} "
                    f"({max(wh, wa):.1%}), Elo gap={abs_elo:.0f}, market agrees"
                )
                return {
                    "scenario": "domination",
                    "reason": (
                        f"Weibull predicts {max(wh, wa):.1%} "
                        f"{'home' if wh > wa else 'away'} win backed by "
                        f"Elo gap {abs_elo:.0f} and market consensus — keep"
                    ),
                    "features": features,
                }

    # ── Rule 3: Extreme probability without fundamental support → market_conflict ──
    if fav_extreme or wd >= WEIBULL_EXTREME_DRAW:
        if market_probs:
            mh = float(market_probs.get("home_prob", 0.33))
            ma = float(market_probs.get("away_prob", 0.33))
            features["market_home"] = round(mh, 4)
            features["market_away"] = round(ma, 4)
            # Check if Weibull extreme conflicts with market
            wb_fav = "home" if wh > wa else "away"
            mkt_fav = "home" if mh > ma else "away"
            if wb_fav != mkt_fav:
                features["trigger"] = (
                    f"Weibull favors {wb_fav} but market favors {mkt_fav}"
                )
                return {
                    "scenario": "market_conflict",
                    "reason": (
                        f"Weibull extreme {max(wh, wa, wd):.1%} conflicts with "
                        f"market direction — shadow mode (50% weight)"
                    ),
                    "features": features,
                }

        # Extreme without Elo or market backing → market_conflict
        features["trigger"] = (
            f"extreme={max(wh, wa, wd):.1%} without fundamental support"
        )
        return {
            "scenario": "market_conflict",
            "reason": (
                f"Weibull extreme ({max(wh, wa, wd):.1%}) without Elo or "
                f"market backing — shadow mode (50% weight)"
            ),
            "features": features,
        }

    # ── Rule 4: Normal range → normal ──
    return {
        "scenario": "normal",
        "reason": "Weibull within normal range",
        "features": features,
    }


def resolve_weibull_action(
    scenario_result: dict[str, Any],
    weibull_weight: float = 0.10,
) -> dict[str, Any]:
    """Convert a scenario classification into a concrete action.

    Returns a dict with keys:
        - ``action``: "keep", "skip", "shadow", or "normal"
        - ``effective_weight``: adjusted Weibull weight to use in fusion
        - ``reason``: human-readable explanation
        - ``scenario``: original scenario name
    """
    scenario = scenario_result.get("scenario", "normal")

    if scenario == "domination":
        return {
            "action": "keep",
            "effective_weight": weibull_weight,
            "reason": scenario_result.get("reason", "Domination: keep Weibull at full weight"),
            "scenario": scenario,
        }
    elif scenario == "draw_noise":
        # V4.8.1: KO draw rate is 25% (4/16) — Weibull draw signals should be
        # preserved as reference (shadow mode) rather than zeroed out (skip).
        # The old "skip" behavior was too aggressive for KO where draws are common.
        shadow_weight = min(
            weibull_weight * (1.0 - WEIBULL_SHADOW_WEIGHT_REDUCTION),
            WEIBULL_MAX_SCENARIO_WEIGHT,
        )
        return {
            "action": "shadow",
            "effective_weight": round(shadow_weight, 4),
            "reason": scenario_result.get("reason", "Draw noise: shadow Weibull at reduced weight"),
            "scenario": scenario,
        }
    elif scenario == "market_conflict":
        shadow_weight = min(
            weibull_weight * (1.0 - WEIBULL_SHADOW_WEIGHT_REDUCTION),
            WEIBULL_MAX_SCENARIO_WEIGHT,
        )
        return {
            "action": "shadow",
            "effective_weight": round(shadow_weight, 4),
            "reason": scenario_result.get("reason", "Market conflict: reduce Weibull to 50%"),
            "scenario": scenario,
        }
    else:
        return {
            "action": "normal",
            "effective_weight": weibull_weight,
            "reason": scenario_result.get("reason", "Normal: full Weibull weight"),
            "scenario": scenario,
        }
