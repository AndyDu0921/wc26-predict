#!/usr/bin/env python3
"""Command-line adapter for the canonical PredictionPipeline."""

from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

if sys.platform == "win32" and "pytest" not in sys.modules:
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer,
        encoding="utf-8",
        errors="replace",
    )

from app.services.canonical_prediction_core import (  # noqa: E402
    PredictionInvocation,
    execute_prediction_core,
)
from app.services.prediction_pipeline import _count_market_providers  # noqa: E402
from app.core.verification_gates import (  # noqa: E402
    preflight_check,
    postflight_check,
    format_gate_results,
    all_errors_passed,
)
from app.services.evaluation_registry import DEFAULT_DB_PATH  # noqa: E402
from app.services.match_resolver import normalize_name  # noqa: E402
from app.services.information_state_engine import (  # noqa: E402
    audit_match_information_state,
    collect_match_evidence,
    extract_information_signals,
    score_information_signals,
)
from app.services.closed_loop_feature_snapshot import (  # noqa: E402
    persist_feature_snapshot_from_latest_prematch,
)
from scripts.audit_match_closed_loop import audit_match_closed_loop  # noqa: E402
from scripts.backfill_prediction_persistence import repair_match as repair_prediction_persistence  # noqa: E402


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
    parser.add_argument("--stage", default="")
    parser.add_argument("--venue")
    parser.add_argument("--non-neutral", action="store_true")
    parser.add_argument("--mode", choices=("baseline", "standard", "full"), default="full")
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--no-market", action="store_true")
    parser.add_argument("--no-weather", action="store_true")
    parser.add_argument("--no-auto-resolve", action="store_true", help=argparse.SUPPRESS)
    persistence = parser.add_mutually_exclusive_group()
    persistence.add_argument("--save", action="store_true",
                             help="Persist snapshot to DB (default: enabled).")
    persistence.add_argument("--no-save", action="store_true",
                             help="Skip DB persistence (debug only).")

    args = parser.parse_args()
    args.home = args.home or args.home_pos
    args.away = args.away or args.away_pos
    if not args.home or not args.away:
        parser.error("home and away teams are required")
    args.competition = (
        args.competition
        or args.competition_pos
        or "FIFA World Cup 2026"
    )

    # Auto-detect match_id from schedule if not provided
    if not args.match_id and not args.no_auto_resolve:
        args.match_id = _find_match_id(args.home, args.away, args.competition)
        if args.match_id:
            print(f"[Auto-resolved] match-id={args.match_id} for {args.home} vs {args.away}")

    if args.match_id and not args.no_auto_resolve:
        _hydrate_match_context(args)

    return args


def _find_match_id(home_team: str, away_team: str, competition: str) -> str:
    """Resolve a unique fixture; never select an arbitrary first team pair."""
    import sqlite3
    db_path = Path(DEFAULT_DB_PATH)
    if not db_path.exists():
        return ""
    try:
        with sqlite3.connect(str(db_path)) as conn:
            rows = []
            if "world cup" in normalize_name(competition):
                rows = conn.execute(
                    "SELECT id FROM wc26_schedule WHERE home_team=? AND away_team=?",
                    (home_team, away_team),
                ).fetchall()
            if len(rows) == 1:
                return str(rows[0][0])
            if len(rows) > 1:
                return ""

            rows = conn.execute(
                """
                SELECT CAST(m.id AS TEXT)
                FROM matches m
                JOIN teams ht ON ht.id=m.home_team_id
                JOIN teams at ON at.id=m.away_team_id
                WHERE ht.name=? AND at.name=? AND m.competition=?
                """,
                (home_team, away_team, competition),
            ).fetchall()
            return str(rows[0][0]) if len(rows) == 1 else ""
    except Exception:
        return ""


def _hydrate_match_context(args: argparse.Namespace) -> None:
    """Fill stage/kickoff/venue from one explicit match identity."""
    import sqlite3

    with sqlite3.connect(str(DEFAULT_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM wc26_schedule WHERE CAST(id AS TEXT)=?",
            (str(args.match_id),),
        ).fetchone()
        if row is not None:
            if normalize_name(row["home_team"]) != normalize_name(args.home) or normalize_name(
                row["away_team"]
            ) != normalize_name(args.away):
                raise SystemExit("match-id team pair does not match --home/--away")
            args.stage = args.stage or str(row["stage"] or "")
            args.venue = args.venue or str(row["venue"] or "")
            if not args.match_date and row["match_date"] and row["kickoff_time"]:
                time_text = str(row["kickoff_time"])
                if len(time_text) == 5:
                    time_text += ":00"
                local = datetime.fromisoformat(f"{row['match_date']}T{time_text}")
                args.match_date = local.replace(tzinfo=ZoneInfo("Asia/Shanghai")).isoformat()
            return

        row = conn.execute(
            """
            SELECT m.match_date, m.stage, m.venue, ht.name AS home_team, at.name AS away_team
            FROM matches m
            JOIN teams ht ON ht.id=m.home_team_id
            JOIN teams at ON at.id=m.away_team_id
            WHERE CAST(m.id AS TEXT)=?
            """,
            (str(args.match_id),),
        ).fetchone()
    if row is None:
        raise SystemExit(f"match-id={args.match_id} was not found")
    if normalize_name(row["home_team"]) != normalize_name(args.home) or normalize_name(
        row["away_team"]
    ) != normalize_name(args.away):
        raise SystemExit("match-id team pair does not match --home/--away")
    args.stage = args.stage or str(row["stage"] or "")
    args.venue = args.venue or str(row["venue"] or "")
    args.match_date = args.match_date or str(row["match_date"] or "")


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


def _materialize_information_state(
    args: argparse.Namespace,
    *,
    as_of_time: str,
) -> dict:
    if not args.no_save:
        collect_match_evidence(
            DEFAULT_DB_PATH,
            match_id=args.match_id or None,
            home_team=args.home,
            away_team=args.away,
            as_of_time=as_of_time,
        )
        extract_information_signals(
            DEFAULT_DB_PATH,
            match_id=args.match_id or None,
            home_team=args.home,
            away_team=args.away,
            kickoff_at=args.match_date,
            as_of_time=as_of_time,
            persist=True,
        )
        score_information_signals(
            DEFAULT_DB_PATH,
            match_id=args.match_id or None,
            home_team=args.home,
            away_team=args.away,
        )
    return audit_match_information_state(
        DEFAULT_DB_PATH,
        match_id=args.match_id or None,
        home_team=args.home,
        away_team=args.away,
        kickoff_at=args.match_date,
        as_of_time=as_of_time,
    )


def main() -> int:
    args = _parse_args()
    is_neutral = not args.non_neutral
    prediction_as_of = datetime.now(UTC).isoformat()

    # ── Pre-flight gate ────────────────────────────────────────────
    preflight_warnings = preflight_check(
        venue_confirmed=bool(args.venue),
        competition_type=args.competition,
        match_stage=args.stage,
    )
    if preflight_warnings:
        print(format_gate_results(preflight_warnings, "Pre-flight Gate"), file=sys.stderr)
        # Non-fatal: continue but with degraded confidence

    information_state_audit = None
    try:
        information_state_audit = _materialize_information_state(
            args,
            as_of_time=prediction_as_of,
        )
        missing = information_state_audit.get("missing", [])
        print(
            "[Information State] "
            f"quality={information_state_audit.get('quality_score', 0):.2f} "
            f"evidence={information_state_audit.get('evidence_count', 0)} "
            f"signals={information_state_audit.get('signal_count', 0)} "
            f"missing={','.join(missing) if missing else 'none'}",
            file=sys.stderr,
        )
    except Exception as exc:
        print(f"[Information State] audit failed: {exc}", file=sys.stderr)

    result = execute_prediction_core(PredictionInvocation(
        home_team=args.home,
        away_team=args.away,
        competition=args.competition,
        is_neutral=is_neutral,
        mode=args.mode,
        match_id=args.match_id,
        kickoff_at=args.match_date,
        stage=args.stage,
        venue=args.venue,
        save_snapshot=not args.no_save,  # Default True; --no-save disables
        enable_market=not args.no_market,
        enable_weather=not args.no_weather,
    ))
    try:
        information_state_audit = _materialize_information_state(
            args,
            as_of_time=datetime.now(UTC).isoformat(),
        )
    except Exception as exc:
        print(f"[Information State] post-prediction evidence capture failed: {exc}", file=sys.stderr)
    payload = result.to_dict()
    if information_state_audit is not None:
        payload["information_state_audit"] = information_state_audit

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
        market_required=not args.no_market,
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

    closed_loop_exit_code = 0
    if not args.no_save and args.match_id:
        import sqlite3

        with sqlite3.connect(str(DEFAULT_DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            payload["prediction_persistence_repair"] = repair_prediction_persistence(
                conn,
                args.match_id,
                persist=True,
            )
        feature_result = persist_feature_snapshot_from_latest_prematch(
            DEFAULT_DB_PATH,
            match_id=args.match_id,
        )
        payload["feature_snapshot_persistence"] = feature_result
        closed_loop_audit = audit_match_closed_loop(
            DEFAULT_DB_PATH,
            match_ids=[args.match_id],
            phase="pre",
        )
        payload["closed_loop_audit"] = closed_loop_audit
        if not closed_loop_audit.get("passed"):
            closed_loop_exit_code = 3
            missing = closed_loop_audit.get("matches", [{}])[0].get("missing", [])
            print(
                "[Closed Loop] incomplete pre-match persistence: "
                + (",".join(missing) if missing else "unknown"),
                file=sys.stderr,
            )

    print("=== PREDICTION JSON ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return closed_loop_exit_code


if __name__ == "__main__":
    raise SystemExit(main())
