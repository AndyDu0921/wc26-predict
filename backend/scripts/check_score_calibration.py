"""Check score calibration drift across all evaluated matches.

V4.7-score S2.3:
  Reads the ``score_calibration_drift`` table and displays per-bucket
  calibration status, including whether any buckets show significant drift.

  A well-calibrated model has ``calibrated_ratio ≈ 1.0`` for every bucket.
  - Ratio > 1.0 → model *underestimates* this total-goals bucket
  - Ratio < 1.0 → model *overestimates* this total-goals bucket

Usage:
  python backend/scripts/check_score_calibration.py [--db-path PATH] [--verbose]
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = BACKEND_DIR / "data" / "local_stage2.db"


def _human_deviation(ratio: float) -> str:
    """Human-readable deviation description."""
    if ratio > 1.15:
        return f"UNDERESTIMATING ({ratio:.2f}x — give more mass)"
    elif ratio > 1.05:
        return f"slightly low  ({ratio:.2f}x)"
    elif ratio < 0.85:
        return f"OVERESTIMATING ({ratio:.2f}x — reduce mass)"
    elif ratio < 0.95:
        return f"slightly high ({ratio:.2f}x)"
    else:
        return f"well-calibrated ({ratio:.2f}x)"


def main(db_path: str | None = None, verbose: bool = False):
    path = Path(db_path) if db_path else DEFAULT_DB
    if not path.exists():
        print(f"Database not found: {path}")
        sys.exit(1)

    # Import after path setup
    sys.path.insert(0, str(BACKEND_DIR))
    from app.services.score_calibration_tracker import (
        ensure_tables,
        get_calibration_summary,
        get_calibration_log,
        BUCKET_LABELS,
    )

    # Ensure tables exist (idempotent)
    ensure_tables(path)

    summary = get_calibration_summary(path)
    buckets = summary["buckets"]
    overall_count = summary["overall_match_count"]
    max_dev = summary["max_deviation"]
    needs = summary["needs_attention"]

    BOLD = "\033[1m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    GREEN = "\033[92m"
    RESET = "\033[0m"

    print(f"\n{BOLD}Score Calibration Drift Report{RESET}")
    print(f"  Matches evaluated: {overall_count}")
    print(f"  Max calibration deviation: {max_dev:.4f}")
    print()

    if not buckets:
        print("  No calibration data yet. Run a post-match evaluation first.")
        return

    # Header
    print(f"{'Bucket':<14} {'Matches':>8} {'Predicted':>10} {'Actual':>8} {'Ratio':>8}  Status")
    print("-" * 70)

    for bucket_name in ["total_0", "total_1", "total_2", "total_3plus"]:
        info = buckets.get(bucket_name)
        if info is None:
            continue
        label = BUCKET_LABELS.get(bucket_name, bucket_name)
        count = info["match_count"]
        pred = info["sum_predicted_prob"]
        actual = info["sum_actual"]
        ratio = info["calibrated_ratio"]
        status = info["status"]

        color = ""
        if status == "significant_drift":
            color = RED
        elif status == "moderate_drift":
            color = YELLOW
        elif status == "well_calibrated":
            color = GREEN

        status_icon = {
            "well_calibrated":    "✓",
            "moderate_drift":     "⚠",
            "significant_drift":  "✗",
            "insufficient_data":  "?",
        }.get(status, "?")

        print(
            f"{label:<14} {count:>8} {pred:>10.4f} {actual:>8.4f} "
            f"{color}{ratio:>8.4f}{RESET}  {status_icon} {status}"
        )

    print("-" * 70)

    if needs:
        print(f"\n{RED}{BOLD}⚠ Buckets needing attention:{RESET}")
        for b in needs:
            info = buckets.get(b, {})
            print(f"  • {BUCKET_LABELS.get(b, b)}: "
                  f"{_human_deviation(info.get('calibrated_ratio', 1.0))}")
    else:
        if overall_count >= 5:
            print(f"\n{GREEN}✓ All buckets well-calibrated.{RESET}")
        else:
            print(f"\n{YELLOW}⚠ Insufficient data (<5 matches) for reliable calibration check.{RESET}")

    # Per-match detail in verbose mode
    if verbose and overall_count > 0:
        print(f"\n{BOLD}Per-Match Calibration Log (most recent 20):{RESET}\n")
        log_entries = get_calibration_log(limit=20, db_path=path)
        if log_entries:
            print(f"{'Match':<8} {'Bucket':<12} {'Pred':>7} {'Act':>4} {'Score':>7}")
            print("-" * 45)
            for entry in log_entries:
                score_str = f"{entry['home_goals']}-{entry['away_goals']}"
                print(
                    f"{str(entry['match_id'])[:8]:<8} "
                    f"{entry['bucket']:<12} "
                    f"{entry['predicted_prob']:>7.4f} "
                    f"{entry['actual']:>4} "
                    f"{score_str:>7}"
                )
        else:
            print("  No log entries found.")

    print()


if __name__ == "__main__":
    db_path = None
    verbose = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--db-path" and i + 1 < len(args):
            db_path = args[i + 1]
            i += 2
        elif args[i] == "--verbose" or args[i] == "-v":
            verbose = True
            i += 1
        else:
            print(f"Usage: python {Path(__file__).name} [--db-path PATH] [--verbose]")
            sys.exit(1)

    main(db_path, verbose)
