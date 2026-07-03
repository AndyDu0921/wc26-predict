#!/usr/bin/env python3
"""Command-line adapter for the canonical PredictionPipeline."""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer,
        encoding="utf-8",
        errors="replace",
    )

from app.services.prediction_pipeline import PredictionPipeline, _count_market_providers
from app.core.verification_gates import (
    preflight_check,
    postflight_check,
    format_gate_results,
    all_errors_passed,
)


def _parse_args() -> argparse.Namespace:
    """Support documented flags and the legacy positional interface."""
    parser = argparse.ArgumentParser(
        description="Run the canonical WC26 prediction pipeline",
    )
    parser.add_argument("home_pos", nargs="?")
    parser.add_argument("away_pos", nargs="?")
    parser.add_argument("competition_pos", nargs="?")
    parser.add_argument("--home")
    parser.add_argument("--away")
    parser.add_argument("--competition")
    parser.add_argument("--match-id", default="")
    parser.add_argument("--match-date")
    parser.add_argument("--venue")
    parser.add_argument("--non-neutral", action="store_true")
    parser.add_argument("--mode", choices=("baseline", "standard", "full"), default="full")
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--no-market", action="store_true")
    parser.add_argument("--no-weather", action="store_true")
    persistence = parser.add_mutually_exclusive_group()
    persistence.add_argument("--save", action="store_true",
                             help="Persist snapshot to DB (default: enabled).")
    persistence.add_argument("--no-save", action="store_true",
                             help="Skip DB persistence (debug only).")

    args = parser.parse_args()
    args.home = args.home or args.home_pos or "Saudi Arabia"
    args.away = args.away or args.away_pos or "Uruguay"
    args.competition = (
        args.competition
        or args.competition_pos
        or "FIFA World Cup 2026"
    )

    # Auto-detect match_id from schedule if not provided
    if not args.match_id:
        args.match_id = _find_match_id(args.home, args.away, args.competition)
        if args.match_id:
            print(f"[Auto-resolved] match-id={args.match_id} for {args.home} vs {args.away}")

    return args


def _find_match_id(home_team: str, away_team: str, competition: str) -> str:
    """Look up the wc26_schedule.id for a match by team names."""
    import sqlite3
    from pathlib import Path as _Path
    db_path = _Path(__file__).resolve().parent.parent / "data" / "local_stage2.db"
    if not db_path.exists():
        return ""
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.execute(
            "SELECT id FROM wc26_schedule WHERE home_team=? AND away_team=? LIMIT 1",
            (home_team, away_team),
        )
        row = cur.fetchone()
        conn.close()
        return str(row[0]) if row else ""
    except Exception:
        return ""


def _bootstrap_payload(home: str, away: str, is_neutral: bool) -> dict | None:
    """Run the optional bootstrap analysis without changing the base prediction."""
    try:
        from scripts._bootstrap_ci import bootstrap_lambda_ci

        result = bootstrap_lambda_ci(
            home,
            away,
            is_neutral=is_neutral,
            n_bootstrap=500,
            seed=42,
        )
    except Exception as exc:
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        return None

    if not result:
        return None
    return {
        "home_win": result["home_win"],
        "draw": result["draw"],
        "away_win": result["away_win"],
        "xg_home": result["bootstrap_xg"]["home"],
        "xg_away": result["bootstrap_xg"]["away"],
        "n_samples": result["n_bootstrap"],
    }


def main() -> int:
    args = _parse_args()
    is_neutral = not args.non_neutral

    # ── Pre-flight gate ────────────────────────────────────────────
    preflight_warnings = preflight_check(
        venue_confirmed=bool(args.venue),
        competition_type=args.competition,
        match_stage="",
    )
    if preflight_warnings:
        print(format_gate_results(preflight_warnings, "Pre-flight Gate"), file=sys.stderr)
        # Non-fatal: continue but with degraded confidence

    pipeline = PredictionPipeline.from_artifacts(mode=args.mode)
    result = pipeline.predict_sync(
        args.home,
        args.away,
        args.competition,
        is_neutral=is_neutral,
        mode=args.mode,
        match_id=args.match_id,
        match_date=args.match_date,
        venue=args.venue,
        save_snapshot=not args.no_save,  # Default True; --no-save disables
        enable_market=not args.no_market,
        enable_weather=not args.no_weather,
    )
    payload = result.to_dict()

    # ── Post-flight gate ───────────────────────────────────────────
    probs_for_gate = payload.get("prediction", {}) if isinstance(payload, dict) else {}
    component_count = (
        len(payload.get("component_probs", {}))
        if isinstance(payload, dict)
        else 0
    )
    market_provider_count = _count_market_providers(result)
    postflight_failures = postflight_check(
        probs=probs_for_gate if probs_for_gate else None,
        all_components_run=component_count,
        market_applied=payload.get("prediction", {}).get("market_applied", False) if isinstance(payload, dict) and isinstance(payload.get("prediction"), dict) else False,
        market_provider_count=market_provider_count,
        calibration_applied=payload.get("calibration_applied", False) if isinstance(payload, dict) else False,
    )
    if postflight_failures:
        print(format_gate_results(postflight_failures, "Post-flight Gate"), file=sys.stderr)
        if not all_errors_passed(postflight_failures):
            print("⛔ Post-flight errors — DB write blocked.", file=sys.stderr)
            # Still print JSON but exit non-zero so callers know it's degraded
            print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
            return 2

    if args.bootstrap:
        payload["bootstrap_ci"] = _bootstrap_payload(
            args.home,
            args.away,
            is_neutral,
        )

    print("=== PREDICTION JSON ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
