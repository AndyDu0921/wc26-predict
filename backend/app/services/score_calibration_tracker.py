"""Score calibration drift tracker — per-bucket reliability monitoring.

V4.7-score S2.3:
  Tracks whether the score probability distribution is well-calibrated over
  time by comparing predicted probability mass against actual frequency for
  each total-goals bucket (0, 1, 2, 3+).

  A well-calibrated model has ``calibrated_ratio ≈ 1.0`` for every bucket.
  Ratio > 1.0 → model underestimates this bucket (need more probability mass).
  Ratio < 1.0 → model overestimates this bucket (need less probability mass).

  Two tables:
    score_calibration_log  — per-match, per-bucket (audit trail, idempotent)
    score_calibration_drift — cumulative running totals (fast dashboard query)

  This is the score-level analogue of ECE (Expected Calibration Error) for
  the three-way H/D/A prediction — but applied to the full score matrix.

References:
  - Wheatcroft (2021, JQAS): proper scoring rules for football scorelines
  - Nipu & McHale (2024): score calibration in betting market contexts
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
DB_PATH = BACKEND_DIR / "data" / "local_stage2.db"

# ── Score bucket definitions ──
# We bucket by total goals (home + away) since this is the dimension most
# directly comparable across matches (unlike individual scorelines which have
# 36 cells and too-sparse data).
BUCKETS = [
    ("total_0",     0, 0),   # 0 total goals (i.e. only 0-0)
    ("total_1",     1, 1),   # 1 total goal  (0-1, 1-0)
    ("total_2",     2, 2),   # 2 total goals (0-2, 1-1, 2-0)
    ("total_3plus", 3, None),  # 3+ total goals (tail — right-censored at max_g)
]
BUCKET_LABELS = {
    "total_0":     "0 goals",
    "total_1":     "1 goal",
    "total_2":     "2 goals",
    "total_3plus": "3+ goals",
}

# ── DDL ──

DDL = """
CREATE TABLE IF NOT EXISTS score_calibration_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id        TEXT NOT NULL,
    snapshot_id     TEXT,
    bucket          TEXT NOT NULL CHECK (bucket IN ('total_0','total_1','total_2','total_3plus')),
    predicted_prob  REAL NOT NULL,
    actual          INTEGER NOT NULL CHECK (actual IN (0, 1)),
    home_goals      INTEGER,
    away_goals      INTEGER,
    total_goals     INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(match_id, bucket)
);

CREATE INDEX IF NOT EXISTS idx_scl_bucket ON score_calibration_log(bucket);
CREATE INDEX IF NOT EXISTS idx_scl_match  ON score_calibration_log(match_id);

CREATE TABLE IF NOT EXISTS score_calibration_drift (
    bucket              TEXT PRIMARY KEY,
    match_count         INTEGER NOT NULL DEFAULT 0,
    sum_predicted_prob  REAL    NOT NULL DEFAULT 0.0,
    sum_actual          REAL    NOT NULL DEFAULT 0.0,
    calibrated_ratio    REAL    NOT NULL DEFAULT 1.0,
    last_updated        TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


# ── Public API ──


def ensure_tables(db_path: str | Path | None = None) -> bool:
    """Create score calibration tables if they don't exist.  Idempotent."""
    path = _resolve_path(db_path)
    if not path.exists():
        logger.warning("DB not found at %s — cannot create score calibration tables", path)
        return False
    try:
        conn = sqlite3.connect(str(path), timeout=0.1)
        conn.executescript(DDL)
        # Seed drift buckets if they don't exist yet
        for bucket_name, _, _ in BUCKETS:
            conn.execute(
                "INSERT OR IGNORE INTO score_calibration_drift (bucket) VALUES (?)",
                (bucket_name,),
            )
        conn.commit()
        conn.close()
        return True
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower():
            logger.debug("Score calibration table creation skipped: database is locked")
            return False
        logger.warning("Failed to create score calibration tables", exc_info=True)
        return False
    except Exception:
        logger.warning("Failed to create score calibration tables", exc_info=True)
        return False


def compute_bucket_probs(
    score_matrix: list[list[float]],
) -> dict[str, float]:
    """Sum the predicted probability mass for each total-goals bucket.

    Parameters
    ----------
    score_matrix:
        (max_g+1)×(max_g+1) score probability matrix, where
        ``matrix[h][a]`` = P(home=h, away=a).

    Returns
    -------
    dict[str, float]
        ``{bucket_name: summed_probability}`` for each of the four buckets.
        Sum across all buckets should ≈ 1.0.
    """
    G = len(score_matrix) - 1
    probs: dict[str, float] = {"total_0": 0.0, "total_1": 0.0, "total_2": 0.0, "total_3plus": 0.0}

    for h in range(G + 1):
        for a in range(G + 1):
            total = h + a
            p = float(score_matrix[h][a])
            if total == 0:
                probs["total_0"] += p
            elif total == 1:
                probs["total_1"] += p
            elif total == 2:
                probs["total_2"] += p
            else:
                probs["total_3plus"] += p

    return probs


def bucket_for_total_goals(total_goals: int) -> str:
    """Return the bucket name for a given total goals count."""
    if total_goals == 0:
        return "total_0"
    elif total_goals == 1:
        return "total_1"
    elif total_goals == 2:
        return "total_2"
    else:
        return "total_3plus"


def log_score_calibration(
    *,
    match_id: str,
    home_goals: int,
    away_goals: int,
    score_matrix: list[list[float]] | None = None,
    snapshot_id: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Record one match's score calibration data and update drift tracking.

    For each total-goals bucket, records the predicted probability mass and
    whether the actual total goals fell in that bucket.  Then updates the
    running drift aggregations.

    Parameters
    ----------
    match_id:
        Match identifier (schedule ID or UUID).
    home_goals, away_goals:
        Actual final score.
    score_matrix:
        (max_g+1)×(max_g+1) fused score probability matrix.  If None or
        empty, only the buckets are seeded and no calibration data is written.
    snapshot_id:
        Optional snapshot ID for traceability.
    db_path:
        Optional DB path override.

    Returns
    -------
    dict
        ``{bucket: {"predicted_prob": float, "actual": int}, ...}``
        Empty dict if no score_matrix provided.
    """
    path = _resolve_path(db_path)
    actual_total = home_goals + away_goals
    actual_bucket = bucket_for_total_goals(actual_total)

    if score_matrix is None or len(score_matrix) == 0:
        logger.debug("No score matrix for match %s — skipping calibration log", match_id)
        return {}

    # Compute per-bucket predicted probabilities
    try:
        bucket_probs = compute_bucket_probs(score_matrix)
    except Exception:
        logger.warning("Failed to compute bucket probs for match %s", match_id, exc_info=True)
        return {}

    # Ensure tables exist
    if not ensure_tables(path):
        return {}

    result: dict[str, Any] = {}
    conn = sqlite3.connect(str(path), timeout=0.1)
    try:
        conn.execute("PRAGMA journal_mode=WAL")

        for bucket_name, predicted_prob in bucket_probs.items():
            actual = 1 if bucket_name == actual_bucket else 0

            # Per-match log (idempotent — replace on conflict)
            conn.execute(
                """INSERT OR REPLACE INTO score_calibration_log
                   (match_id, snapshot_id, bucket, predicted_prob, actual,
                    home_goals, away_goals, total_goals, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (str(match_id), snapshot_id, bucket_name,
                 round(predicted_prob, 8), actual,
                 home_goals, away_goals, actual_total),
            )

            result[bucket_name] = {"predicted_prob": round(predicted_prob, 6), "actual": actual}

        # ── Update drift table ──
        # Use a two-pass approach: first delete all drift rows whose matches
        # are counted, then rebuild from the log.  This handles the re-run
        # (idempotent) case correctly — the log row was just replaced, now
        # the drift tables are rebuilt from the up-to-date log.
        _rebuild_drift_from_log(conn)

        conn.commit()
    except sqlite3.OperationalError as exc:
        conn.rollback()
        if "locked" in str(exc).lower():
            logger.debug("Score calibration log skipped for match %s: database is locked", match_id)
            return result
        logger.warning("Failed to log score calibration for match %s", match_id, exc_info=True)
        return result
    except Exception:
        conn.rollback()
        logger.warning("Failed to log score calibration for match %s", match_id, exc_info=True)
        return result
    finally:
        conn.close()

    return result


def get_calibration_summary(
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Read the current score calibration drift state.

    Returns
    -------
    dict
        {
            "buckets": {bucket_name: {match_count, sum_predicted_prob,
                        sum_actual, calibrated_ratio, label, status}},
            "overall_match_count": int,
            "max_deviation": float (largest |ratio - 1.0|),
            "needs_attention": [bucket_name, ...] (buckets with |ratio - 1.0| > 0.20),
        }
    """
    path = _resolve_path(db_path)
    if not path.exists():
        return {"buckets": {}, "overall_match_count": 0, "max_deviation": 0.0, "needs_attention": []}

    conn = sqlite3.connect(str(path))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT bucket, match_count, sum_predicted_prob, sum_actual, calibrated_ratio "
            "FROM score_calibration_drift ORDER BY bucket"
        )
        rows = cur.fetchall()

        buckets: dict[str, dict[str, Any]] = {}
        overall_count = 0
        max_deviation = 0.0
        needs_attention: list[str] = []

        for bucket, count, sum_pred, sum_act, ratio in rows:
            count = int(count)
            sum_pred = float(sum_pred)
            sum_act = float(sum_act)
            ratio = float(ratio)

            deviation = abs(ratio - 1.0)
            if deviation > max_deviation:
                max_deviation = deviation

            # Status based on ratio and sample size
            if count < 5:
                status = "insufficient_data"
            elif deviation < 0.10:
                status = "well_calibrated"
            elif deviation < 0.20:
                status = "moderate_drift"
            else:
                status = "significant_drift"
                needs_attention.append(bucket)

            buckets[bucket] = {
                "match_count": count,
                "sum_predicted_prob": round(sum_pred, 6),
                "sum_actual": round(sum_act, 6),
                "calibrated_ratio": round(ratio, 4),
                "label": BUCKET_LABELS.get(bucket, bucket),
                "status": status,
            }
            if count > overall_count:
                overall_count = count

    finally:
        conn.close()

    return {
        "buckets": buckets,
        "overall_match_count": overall_count,
        "max_deviation": round(max_deviation, 4),
        "needs_attention": needs_attention,
    }


def get_calibration_log(
    *,
    match_id: str | None = None,
    bucket: str | None = None,
    limit: int = 20,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Read per-match calibration log entries.

    Parameters
    ----------
    match_id:
        Filter to a specific match. If None, returns all matches.
    bucket:
        Filter to a specific bucket. If None, returns all buckets.
    limit:
        Max rows to return (most recent first).
    db_path:
        Optional DB path override.

    Returns
    -------
    list[dict]
        Calibration log entries sorted by created_at DESC.
    """
    path = _resolve_path(db_path)
    if not path.exists():
        return []

    conn = sqlite3.connect(str(path))
    try:
        cur = conn.cursor()
        where_clauses: list[str] = []
        params: list[Any] = []
        if match_id is not None:
            where_clauses.append("match_id = ?")
            params.append(str(match_id))
        if bucket is not None:
            where_clauses.append("bucket = ?")
            params.append(bucket)

        sql = "SELECT match_id, snapshot_id, bucket, predicted_prob, actual, home_goals, away_goals, total_goals, created_at FROM score_calibration_log"
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cur.execute(sql, params)
        return [
            {
                "match_id": r[0],
                "snapshot_id": r[1],
                "bucket": r[2],
                "predicted_prob": round(float(r[3]), 6),
                "actual": int(r[4]),
                "home_goals": r[5],
                "away_goals": r[6],
                "total_goals": r[7],
                "created_at": r[8],
            }
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


# ── Internal helpers ──


def _resolve_path(db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    return DB_PATH


def _rebuild_drift_from_log(conn: sqlite3.Connection) -> None:
    """Rebuild the score_calibration_drift table from the per-match log.

    Called after each INSERT OR REPLACE so that re-running for the same match
    produces correct cumulative totals (the old entry was replaced, now we
    recompute from the updated log).

    Uses a simple SUM aggregation — each match contributes one row per bucket,
    and the bucket-level UNIQUE constraint guarantees one entry per (match_id,
    bucket) pair.
    """
    cur = conn.cursor()

    # Check which matches have been deleted (removed from log) → nothing to do,
    # we only ever INSERT OR REPLACE (never DELETE).  So a full rebuild from
    # the log is correct and idempotent.

    for bucket_name, _, _ in BUCKETS:
        cur.execute(
            "SELECT COUNT(*), SUM(predicted_prob), SUM(actual) "
            "FROM score_calibration_log WHERE bucket = ?",
            (bucket_name,),
        )
        row = cur.fetchone()
        count = int(row[0]) if row and row[0] is not None else 0
        sum_pred = float(row[1]) if row and row[1] is not None else 0.0
        sum_act = float(row[2]) if row and row[2] is not None else 0.0

        ratio = sum_act / sum_pred if sum_pred > 0 else 1.0

        conn.execute(
            """INSERT OR REPLACE INTO score_calibration_drift
               (bucket, match_count, sum_predicted_prob, sum_actual,
                calibrated_ratio, last_updated)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (bucket_name, count, round(sum_pred, 8), round(sum_act, 8), round(ratio, 6)),
        )
