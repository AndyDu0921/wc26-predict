"""verification_gates.py — Declarative pre/post-flight check gates.

Harness layer 5 (Policy/Guardrails): prevents incomplete predictions from
entering the database or being published in reports.  Inspired by the
"Gate" concept from Mitchell Hashimoto's Harness Engineering framework.

All functions are pure (no I/O, no side effects) so they can be called from
CLI scripts, the API, notebook cells, or an Agent (Claude) without pulling
in database or network dependencies.

Usage::

    from app.core.verification_gates import preflight_check, postflight_check

    # Before prediction
    warnings = preflight_check(
        home_elo=1684.0,
        away_elo=1500.0,
        weibull_available=True,
        venue_confirmed=True,
        injuries_loaded=True,
        competition_weight=1.5,
    )
    if warnings:
        for w in warnings:
            print(f"⚠️  {w.gate}: {w.message}")

    # After prediction, before DB write
    failures = postflight_check(
        probs={"home_win_prob": 0.45, "draw_prob": 0.25, "away_win_prob": 0.30},
        all_components_run=7,
        market_applied=True,
        market_provider_count=5,
        calibration_applied=True,
        is_knockout=False,
        elo_gap=184,
    )
    if failures:
        for f in failures:
            print(f"❌ {f.gate}: {f.message}")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["error", "warning", "info"]


@dataclass
class GateResult:
    """Single check outcome."""
    gate: str
    passed: bool
    severity: Severity = "warning"
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "passed": self.passed,
            "severity": self.severity,
            "message": self.message,
            "detail": self.detail,
        }


# ── Pre-flight checks (run BEFORE prediction) ──────────────────────


def preflight_check(
    *,
    home_elo: float | None = None,
    away_elo: float | None = None,
    weibull_available: bool | None = None,
    venue_confirmed: bool = False,
    injuries_loaded: bool = False,
    competition_weight: float | None = None,
    competition_type: str = "",
    match_stage: str = "",
) -> list[GateResult]:
    """Run all pre-flight checks.  Returns only warnings/failures (no passed).

    Call this before starting a prediction.  Non-fatal by design — warnings
    reduce confidence but don't block the pipeline.  Callers should attach
    failing gates to ``degraded_reasons`` in the PredictionResult.

    NOTE: market_provider_count is NOT checked here — market data is fetched
    DURING the pipeline, so preflight can't know the count.  Use
    postflight_check() with the actual count from the pipeline result.
    """
    results: list[GateResult] = []

    # 1. Elo default detection
    results.append(
        _check_elo_not_default(home_elo or 0.0, away_elo or 0.0)
    )

    # 2. Weibull component status
    results.append(
        _check_weibull_status(weibull_available)
    )

    # 4. Venue confirmation
    results.append(
        _check_venue_confirmed(venue_confirmed)
    )

    # 5. Injuries data
    results.append(
        _check_injuries_loaded(injuries_loaded)
    )

    # 6. Competition weight sanity
    results.append(
        _check_competition_weight(competition_weight, competition_type, match_stage)
    )

    return [r for r in results if not r.passed]


def _check_elo_not_default(home_elo: float, away_elo: float) -> GateResult:
    """Bug: CIV Elo=1500 default caused 29.7pp market divergence."""
    DEFAULT = 1500.0
    issues: list[str] = []
    if abs(home_elo - DEFAULT) < 0.01:
        issues.append("home team")
    if abs(away_elo - DEFAULT) < 0.01:
        issues.append("away team")

    if not issues:
        return GateResult(
            gate="elo_default_check",
            passed=True,
            severity="info",
            message="Both teams have non-default Elo ratings.",
        )

    return GateResult(
        gate="elo_default_check",
        passed=False,
        severity="warning",
        message=f"Default Elo (1500) detected for {', '.join(issues)}. "
                f"System unfamiliar with this team — all component attack/defense "
                f"estimates may be unreliable.  Reduce confidence.",
        detail={
            "home_elo": home_elo,
            "away_elo": away_elo,
            "affected_teams": issues,
            "bug_ref": "CIV Elo=1500 → 29.7pp divergence",
        },
    )


def _check_market_provider_count(count: int) -> GateResult:
    """Bug: single-bookmaker data is unreliable. Need ≥3 for consensus."""
    if count >= 3:
        return GateResult(
            gate="market_provider_count",
            passed=True,
            severity="info",
            message=f"Market data from {count} providers (≥3 ✓).",
        )
    if count >= 1:
        return GateResult(
            gate="market_provider_count",
            passed=False,
            severity="warning",
            message=f"Only {count} market provider(s) — insufficient for "
                    f"robust consensus.  Start WebSearch fallback.  "
                    f"market_max capped at 15%.",
            detail={
                "provider_count": count,
                "required": 3,
                "bug_ref": "single bookmaker unreliable",
            },
        )
    return GateResult(
        gate="market_provider_count",
        passed=False,
        severity="error",
        message="No market data available.  market_max will be 0 — "
                "pure model prediction only.  Mark as degraded.",
        detail={
            "provider_count": 0,
            "required": 3,
            "bug_ref": "market data missing entirely",
        },
    )


def _check_weibull_status(available: bool | None) -> GateResult:
    """Bug: Weibull silently skipped without recording the skip."""
    if available:
        return GateResult(
            gate="weibull_status",
            passed=True,
            severity="info",
            message="Weibull component available.",
        )
    return GateResult(
        gate="weibull_status",
        passed=False,
        severity="warning",
        message="Weibull component NOT available (timeout / fitting failed / "
                "insufficient data).  Report MUST explicitly state 'Weibull "
                "not included for this match'.  Do not silently skip.",
        detail={
            "available": False,
            "bug_ref": "V4.3.1-fix: Weibull silently skipped",
        },
    )


def _check_venue_confirmed(confirmed: bool) -> GateResult:
    """Bug: DB venue error (Estadio Akron → Azteca) — never trust DB blindly."""
    if confirmed:
        return GateResult(
            gate="venue_confirmed",
            passed=True,
            severity="info",
            message="Venue confirmed via WebSearch cross-validation.",
        )
    return GateResult(
        gate="venue_confirmed",
        passed=False,
        severity="warning",
        message="Venue NOT confirmed via WebSearch.  DB venue field may "
                "contain errors (historical: Estadio Akron was wrong).  "
                "Run WebSearch before publishing report.",
        detail={
            "confirmed": False,
            "bug_ref": "V4.3.9: DB venue error (Akron→Azteca)",
        },
    )


def _check_injuries_loaded(loaded: bool) -> GateResult:
    """Bug: injuries.json was empty; Amoura/Montiel errors in past reports."""
    if loaded:
        return GateResult(
            gate="injuries_loaded",
            passed=True,
            severity="info",
            message="Injury data loaded from ≥1 source.",
        )
    return GateResult(
        gate="injuries_loaded",
        passed=False,
        severity="warning",
        message="No injury data loaded.  Report must not mention specific "
                "injuries.  Mark injury section as 'data unavailable'.",
        detail={
            "loaded": False,
            "bug_ref": "Amoura (Jordan-Algeria) / Montiel (Argentina-Austria) errors",
        },
    )


def _check_competition_weight(
    weight: float | None,
    competition: str,
    stage: str,
) -> GateResult:
    """Bug 16/22/24: WC matches hardcoded 0.5 instead of 1.5."""
    if weight is None:
        return GateResult(
            gate="competition_weight",
            passed=True,
            severity="info",
            message="Competition weight will be auto-detected.",
        )

    is_wc = "world cup" in competition.lower()
    is_ko = any(kw in stage.lower() for kw in
                ["round of", "quarter", "semi", "final", "playoff", "knockout",
                 "last 16", "last 32", "last 8"])

    if is_wc and weight < 1.0:
        return GateResult(
            gate="competition_weight",
            passed=False,
            severity="error",
            message=f"World Cup competition_weight={weight} — too low!  "
                    f"WC matches should use 1.5.  Hardcoded 0.5 is a "
                    f"known bug (Bug 16/22/24).",
            detail={
                "current_weight": weight,
                "expected": 1.5,
                "competition": competition,
                "stage": stage,
                "is_wc": is_wc,
                "is_knockout": is_ko,
                "bug_ref": "Bug 16/22/24",
            },
        )

    if is_wc and weight >= 1.0:
        return GateResult(
            gate="competition_weight",
            passed=True,
            severity="info",
            message=f"WC competition_weight={weight} ✓.",
        )

    return GateResult(
        gate="competition_weight",
        passed=True,
        severity="info",
        message=f"competition_weight={weight} (non-WC competition).",
    )


# ── Post-flight checks (run AFTER prediction, BEFORE DB write) ─────


def postflight_check(
    *,
    probs: dict[str, float] | None = None,
    all_components_run: int = 0,
    market_applied: bool = False,
    market_provider_count: int = 0,
    calibration_applied: bool = False,
    is_knockout: bool = False,
    elo_gap: float | None = None,
) -> list[GateResult]:
    """Run all post-flight checks.  Returns only failures.

    Call this after the fusion chain completes, before writing to DB or
    publishing a report.  Failures here SHOULD block DB writes.
    """
    results: list[GateResult] = []

    # 1. All 7 components computed
    results.append(_check_all_components_run(all_components_run))

    # 2. No extreme (0%/100%) probabilities
    if probs is not None:
        results.append(_check_no_extreme_probs(probs))

    # 3. Draw floor enforced (WC)
    if probs is not None:
        results.append(_check_draw_floor(probs, is_knockout))

    # 4. Market boost applied
    results.append(_check_market_applied(market_applied))

    # 5. Market data provider count (must run AFTER pipeline — count is unknown preflight)
    results.append(_check_market_provider_count(market_provider_count))

    # 6. Calibration applied
    results.append(_check_calibration_applied(calibration_applied))

    # 7. Probabilities sum to 1
    if probs is not None:
        results.append(_check_probs_sum_to_one(probs))

    # 8. Knockout draw underestimation check
    if probs is not None and is_knockout and elo_gap is not None:
        results.append(_check_ko_draw_underestimation(probs, elo_gap))

    return [r for r in results if not r.passed]


def _check_all_components_run(count: int) -> GateResult:
    """Pipeline has 7 components: DC, Enhancer, NegBin, Weibull, Elo, Pi, Market."""
    if count >= 7:
        return GateResult(
            gate="all_components_run",
            passed=True,
            severity="info",
            message=f"All {count}/7 components executed.",
        )
    if count >= 5:
        return GateResult(
            gate="all_components_run",
            passed=False,
            severity="warning",
            message=f"Only {count}/7 components ran.  Missing components "
                    f"must be declared in the report with explicit reason.",
            detail={"components_run": count, "expected": 7},
        )
    return GateResult(
        gate="all_components_run",
        passed=False,
        severity="error",
        message=f"Only {count}/7 components ran — prediction is severely "
                f"degraded.  Do NOT write to DB as a complete prediction.  "
                f"Mark as 'partial' with explicit list of missing components.",
        detail={"components_run": count, "expected": 7, "minimum": 5},
    )


def _check_no_extreme_probs(probs: dict[str, float]) -> GateResult:
    """Bug: V4.3.1 calibrator produced 0% probabilities before MIN_PROB=0.02."""
    MIN_PROB = 0.02
    h = probs.get("home_win_prob", probs.get("home", 0.33))
    d = probs.get("draw_prob", probs.get("draw", 0.33))
    a = probs.get("away_win_prob", probs.get("away", 0.33))

    extremes: list[str] = []
    if h <= MIN_PROB:
        extremes.append(f"home={h:.4f}")
    if d <= MIN_PROB:
        extremes.append(f"draw={d:.4f}")
    if a <= MIN_PROB:
        extremes.append(f"away={a:.4f}")
    if h >= 0.99:
        extremes.append(f"home={h:.4f}")
    if d >= 0.99:
        extremes.append(f"draw={d:.4f}")
    if a >= 0.99:
        extremes.append(f"away={a:.4f}")

    if not extremes:
        return GateResult(
            gate="no_extreme_probs",
            passed=True,
            severity="info",
            message="All probabilities in [0.02, 0.98] ✓.",
        )

    return GateResult(
        gate="no_extreme_probs",
        passed=False,
        severity="error",
        message=f"Extreme probabilities detected: {', '.join(extremes)}.  "
                f"MIN_PROB=0.02 safety clip should have prevented this — "
                f"check that calibration was applied AFTER draw floor.",
        detail={
            "home": h,
            "draw": d,
            "away": a,
            "min_prob": MIN_PROB,
            "bug_ref": "V4.3.1 extreme clipping",
        },
    )


def _check_draw_floor(probs: dict[str, float], is_knockout: bool) -> GateResult:
    """Bug: KO draw systematic underestimation (50% actual vs ~20% predicted)."""
    d = probs.get("draw_prob", probs.get("draw", 0.33))
    min_draw = 0.18 if is_knockout else 0.12

    if d >= min_draw:
        return GateResult(
            gate="draw_floor",
            passed=True,
            severity="info",
            message=f"Draw probability {d:.1%} ≥ {min_draw:.0%} ✓.",
        )

    return GateResult(
        gate="draw_floor",
        passed=False,
        severity="error" if is_knockout else "warning",
        message=(
            f"Draw probability {d:.1%} below floor {min_draw:.0%}.  "
            + (
                "KO matches have 50% actual draw rate -- this is likely "
                "an underestimation (Bug: GER-PAR + NED-MAR)."
                if is_knockout
                else "DRAW_FLOOR=0.12 should have prevented this -- check "
                "enforce_draw_floor() was called."
            )
        ),
        detail={
            "draw_prob": d,
            "floor": min_draw,
            "is_knockout": is_knockout,
            "bug_ref": "GER-PAR + NED-MAR: KO draw underestimation",
        },
    )


def _check_market_applied(applied: bool) -> GateResult:
    """Bug: R4-C3 market_applied key destroyed in orchestrator."""
    if applied:
        return GateResult(
            gate="market_applied",
            passed=True,
            severity="info",
            message="Market boost check: applied ✓.",
        )
    return GateResult(
        gate="market_applied",
        passed=False,
        severity="warning",
        message="market_applied=False.  If market data was available but not "
                "applied, this may be a bug.  If market data was unavailable, "
                "this must be declared in report.",
        detail={
            "applied": False,
            "bug_ref": "R4-C3: market_applied key destroyed",
        },
    )


def _check_calibration_applied(applied: bool) -> GateResult:
    """Bug: R4-C9 calibration_applied was never set to True."""
    if applied:
        return GateResult(
            gate="calibration_applied",
            passed=True,
            severity="info",
            message="Post-fusion calibration: applied ✓.",
        )
    return GateResult(
        gate="calibration_applied",
        passed=False,
        severity="error",
        message="calibration_applied=False.  Post-fusion Isotonic calibration "
                "is mandatory for WC predictions.  This is a pipeline bug if "
                "calibrator was available but not called (Bug R4-C9).",
        detail={
            "applied": False,
            "bug_ref": "R4-C9: calibration_applied never True",
        },
    )


def _check_probs_sum_to_one(probs: dict[str, float]) -> GateResult:
    """Probabilities must sum to 1.0 within tolerance."""
    h = probs.get("home_win_prob", probs.get("home", 0.33))
    d = probs.get("draw_prob", probs.get("draw", 0.33))
    a = probs.get("away_win_prob", probs.get("away", 0.33))
    total = h + d + a
    if abs(total - 1.0) < 0.005:
        return GateResult(
            gate="probs_sum_to_one",
            passed=True,
            severity="info",
            message=f"Probabilities sum to {total:.4f} ≈ 1.0 ✓.",
        )
    return GateResult(
        gate="probs_sum_to_one",
        passed=False,
        severity="error",
        message=f"Probabilities sum to {total:.4f}, not 1.0.  "
                f"Normalization step was skipped or broken.",
        detail={"home": h, "draw": d, "away": a, "sum": total, "tolerance": 0.005},
    )


def _check_ko_draw_underestimation(
    probs: dict[str, float],
    elo_gap: float,
) -> GateResult:
    """Bug: KO draw systematically underestimated when Elo gap is small."""
    d = probs.get("draw_prob", probs.get("draw", 0.33))

    if elo_gap > 50:
        return GateResult(
            gate="ko_draw_underestimation",
            passed=True,
            severity="info",
            message=f"KO draw check: Elo gap {elo_gap:.0f} > 50 — "
                    f"low draw probability may be justified.",
        )

    if d >= 0.22:
        return GateResult(
            gate="ko_draw_underestimation",
            passed=True,
            severity="info",
            message=f"KO draw {d:.1%} ≥ 22% with Elo gap {elo_gap:.0f} ✓.",
        )

    return GateResult(
        gate="ko_draw_underestimation",
        passed=False,
        severity="warning",
        message=f"KO match with Elo gap only {elo_gap:.0f} (<50) but draw "
                f"probability only {d:.1%} (<22%).  This pattern produced "
                f"2 false negatives in GER-PAR and NED-MAR.  Consider "
                f"manual review and possible draw boost.",
        detail={
            "draw_prob": d,
            "elo_gap": elo_gap,
            "threshold_gap": 50,
            "threshold_draw": 0.22,
            "bug_ref": "KO draw systematic underestimation (GER-PAR + NED-MAR)",
        },
    )


# ── Post-match review gates (run BEFORE post-match) ─────────────────


def postmatch_check(
    *,
    score_sources: int = 0,
    snapshot_exists: bool = False,
    snapshot_is_complete: bool = False,
    previous_learning_log_conflict: bool = False,
) -> list[GateResult]:
    """Verify preconditions before running post-match review.

    Returns only failures.  Failing gates should BLOCK the review —
    you can't review a match without knowing the actual score.
    """
    results: list[GateResult] = []

    results.append(_check_score_sources(score_sources))
    results.append(_check_snapshot_exists(snapshot_exists))
    results.append(_check_snapshot_complete(snapshot_is_complete))
    results.append(_check_learning_log_conflict(previous_learning_log_conflict))

    return [r for r in results if not r.passed]


def _check_score_sources(sources: int) -> GateResult:
    """Bug: R4-C10 verification gate never passed (required ≥2 sources)."""
    if sources >= 2:
        return GateResult(
            gate="score_sources",
            passed=True,
            severity="info",
            message=f"Score confirmed from {sources} independent sources ✓.",
        )
    if sources == 1:
        return GateResult(
            gate="score_sources",
            passed=False,
            severity="warning",
            message="Score from only 1 source — verify with WebSearch "
                    "before proceeding.",
            detail={"sources": 1, "required": 2, "bug_ref": "R4-C10"},
        )
    return GateResult(
        gate="score_sources",
        passed=False,
        severity="error",
        message="No score sources available.  Cannot run post-match review "
                "without confirmed match result.",
        detail={"sources": 0, "required": 2, "bug_ref": "R4-C10"},
    )


def _check_snapshot_exists(exists: bool) -> GateResult:
    """Prediction snapshot must exist to compare against."""
    if exists:
        return GateResult(
            gate="snapshot_exists",
            passed=True,
            severity="info",
            message="Prediction snapshot found for this match ✓.",
        )
    return GateResult(
        gate="snapshot_exists",
        passed=False,
        severity="error",
        message="No prediction snapshot for this match.  Cannot run "
                "post-match review — nothing to compare against.",
        detail={"exists": False},
    )


def _check_snapshot_complete(complete: bool) -> GateResult:
    """Snapshot must contain all 7 component probabilities."""
    if complete:
        return GateResult(
            gate="snapshot_complete",
            passed=True,
            severity="info",
            message="Snapshot is complete (all components present) ✓.",
        )
    return GateResult(
        gate="snapshot_complete",
        passed=False,
        severity="warning",
        message="Snapshot is incomplete — some component probabilities "
                "may be missing.  Post-match evaluation may be partial.",
        detail={"complete": False},
    )


# ── Snapshot completeness verification ───────────────────────────────


# The 7 fusion components expected in every complete prediction snapshot.
REQUIRED_COMPONENT_KEYS: tuple[str, ...] = (
    "dixon_coles",
    "enhancer",
    "negbin",
    "weibull",
    "elo",
    "pi_rating",
    "market",
)

# Alternative key names accepted for each required component.
_COMPONENT_KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "dixon_coles": ("dc",),
    "enhancer": ("enh",),
    "negbin": ("neg_bin", "negative_binomial"),
    "weibull": ("weibull_",),
    "elo": ("elo_rating",),
    "pi_rating": ("pi", "pi_rating"),
    "market": ("market_consensus", "market_implied"),
}


def verify_snapshot_completeness(
    component_probs: dict[str, Any] | None = None,
    *,
    home_xg: float | None = None,
    away_xg: float | None = None,
    market_blended: bool | None = None,
    market_weight_used: float | None = None,
    market_divergence: float | None = None,
    component_count: int | None = None,
) -> tuple[bool, list[str], list[GateResult]]:
    """Verify that a prediction snapshot has all required data for self-evolution.

    Unlike ``_check_snapshot_complete`` which takes a pre-computed boolean,
    this function INSPECTS the actual snapshot data to determine completeness.

    Checks performed:
        1. All 7 component probabilities present (DC, Enhancer, NegBin,
           Weibull, Elo, Pi, Market)
        2. xG values non-None (home_xg, away_xg)
        3. Market blending metadata present (market_blended, weight, divergence)

    Returns:
        (is_complete, missing_fields, gate_results)

    Usage:
        is_complete, missing, gates = verify_snapshot_completeness(
            component_probs=snapshot_dict,
            home_xg=1.2, away_xg=0.8,
            market_blended=True, market_weight_used=0.30,
        )
        if not is_complete:
            raise RuntimeError(
                f"Snapshot incomplete — missing: {', '.join(missing)}"
            )
    """
    missing: list[str] = []
    gates: list[GateResult] = []

    # ── 1. Component probabilities ──
    if component_probs is None:
        missing.append("component_probs (entire dict is None)")
        gates.append(GateResult(
            gate="snapshot_components",
            passed=False,
            severity="error",
            message="component_probs is None — no model outputs recorded.",
            detail={"component_probs": None},
        ))
    else:
        # Check each required component key
        comp_keys_lower = {k.lower(): k for k in component_probs}
        for req_key in REQUIRED_COMPONENT_KEYS:
            found = False
            # Direct match
            if req_key in component_probs:
                found = True
            # Case-insensitive match
            elif req_key.lower() in comp_keys_lower:
                found = True
            # Alias match
            else:
                aliases = _COMPONENT_KEY_ALIASES.get(req_key, ())
                for alias in aliases:
                    if alias in component_probs or alias.lower() in comp_keys_lower:
                        found = True
                        break
            if not found:
                missing.append(f"component_probs.{req_key}")

        if missing:
            gates.append(GateResult(
                gate="snapshot_components",
                passed=False,
                severity="error",
                message=f"Missing component probabilities: {', '.join(missing)}. "
                        f"Found: {', '.join(sorted(component_probs.keys()))}.",
                detail={
                    "required": list(REQUIRED_COMPONENT_KEYS),
                    "found": sorted(component_probs.keys()),
                    "missing": missing.copy(),
                },
            ))
        else:
            gates.append(GateResult(
                gate="snapshot_components",
                passed=True,
                severity="info",
                message=f"All {len(REQUIRED_COMPONENT_KEYS)} component probabilities present ✓.",
            ))

    # ── 2. xG values ──
    xg_missing: list[str] = []
    if home_xg is None:
        xg_missing.append("home_xg")
    if away_xg is None:
        xg_missing.append("away_xg")
    if xg_missing:
        missing.extend(xg_missing)
        gates.append(GateResult(
            gate="snapshot_xg",
            passed=False,
            severity="warning",
            message=f"xG values missing: {', '.join(xg_missing)}.  "
                    f"Process evaluation requires xG for dominance_index.",
            detail={"home_xg": home_xg, "away_xg": away_xg, "missing": xg_missing},
        ))
    else:
        gates.append(GateResult(
            gate="snapshot_xg",
            passed=True,
            severity="info",
            message=f"xG values present (home={home_xg:.2f}, away={away_xg:.2f}) ✓.",
        ))

    # ── 3. Market blending metadata ──
    market_missing: list[str] = []
    if market_blended is None:
        market_missing.append("market_blended")
    if market_weight_used is None:
        market_missing.append("market_weight_used")
    if market_divergence is None:
        market_missing.append("market_divergence")
    if market_missing:
        missing.extend(market_missing)
        gates.append(GateResult(
            gate="snapshot_market_metadata",
            passed=False,
            severity="warning",
            message=f"Market blending metadata missing: {', '.join(market_missing)}.  "
                    f"Post-match analysis cannot assess market contribution.",
            detail={
                "market_blended": market_blended,
                "market_weight_used": market_weight_used,
                "market_divergence": market_divergence,
            },
        ))
    else:
        gates.append(GateResult(
            gate="snapshot_market_metadata",
            passed=True,
            severity="info",
            message="Market blending metadata present ✓.",
        ))

    # ── 4. Component count (supplementary check) ──
    if component_count is not None and component_count < 5:
        gates.append(GateResult(
            gate="snapshot_component_count",
            passed=False,
            severity="error",
            message=f"Only {component_count} components ran — snapshot is degraded.",
            detail={"component_count": component_count, "minimum": 5},
        ))
        if "component_count" not in missing:
            missing.append(f"component_count={component_count} (<5)")

    is_complete = len(missing) == 0
    return is_complete, missing, gates


def _check_learning_log_conflict(conflict: bool) -> GateResult:
    """Detect if this match already has a learning log (idempotency check)."""
    if not conflict:
        return GateResult(
            gate="learning_log_conflict",
            passed=True,
            severity="info",
            message="No conflicting learning log found ✓.",
        )
    return GateResult(
        gate="learning_log_conflict",
        passed=False,
        severity="warning",
        message="Previous learning log found for this match.  Re-running "
                "will overwrite — this is fine (idempotent) but confirm "
                "you're not double-counting in aggregate stats.",
        detail={"conflict": True},
    )


# ── Convenience: report formatter ───────────────────────────────────


def format_gate_results(
    results: list[GateResult],
    title: str = "Gate Check Results",
) -> str:
    """Format a list of gate results into a human-readable report."""
    errors = [r for r in results if r.severity == "error"]
    warnings = [r for r in results if r.severity == "warning"]

    lines = [f"── {title} ──", ""]
    lines.append(f"  Errors:   {len(errors)}")
    lines.append(f"  Warnings: {len(warnings)}")
    lines.append(f"  Total:    {len(results)}")
    lines.append("")

    if errors:
        lines.append("  ❌ Errors (should block):")
        for r in errors:
            lines.append(f"     [{r.gate}] {r.message}")
        lines.append("")

    if warnings:
        lines.append("  ⚠️  Warnings (should reduce confidence):")
        for r in warnings:
            lines.append(f"     [{r.gate}] {r.message}")
        lines.append("")

    if not errors and not warnings:
        lines.append("  ✅ All checks passed.")

    return "\n".join(lines)


def all_errors_passed(results: list[GateResult]) -> bool:
    """Return True if NO gate has severity='error' — i.e. safe to write to DB."""
    return not any(r.severity == "error" for r in results)
