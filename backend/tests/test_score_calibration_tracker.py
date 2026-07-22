"""Tests for score calibration drift tracking (V4.7 S2.3).

Verifies:
  - Bucket probability computation
  - Bucket assignment from total goals
  - Per-match log + cumulative drift update
  - Idempotency (re-running for same match)
  - Calibration summary with correct ratios
  - Edge cases: empty matrix, goals beyond max_g, missing DB
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

# Module under test
from app.services.score_calibration_tracker import (
    BUCKETS,
    BUCKET_LABELS,
    bucket_for_total_goals,
    compute_bucket_probs,
    ensure_tables,
    get_calibration_log,
    get_calibration_summary,
    log_score_calibration,
)


# ── Fixtures ──────────────────────────────────────────────────────


def _make_uniform_matrix(g: int = 5) -> list[list[float]]:
    """Create a uniform score matrix — every cell has equal probability."""
    size = (g + 1) * (g + 1)
    p = 1.0 / size
    return [[p] * (g + 1) for _ in range(g + 1)]


def _make_dirac_matrix(h: int, a: int, g: int = 5) -> list[list[float]]:
    """Create a score matrix where only cell (h, a) has probability 1.0."""
    mat = [[0.0] * (g + 1) for _ in range(g + 1)]
    mat[h][a] = 1.0
    return mat


def _make_realistic_matrix(g: int = 5) -> list[list[float]]:
    """A realistic Poisson-like score matrix: 2-1 is most likely, then 1-1, etc."""
    mat = [[0.0] * (g + 1) for _ in range(g + 1)]
    # Assign rough probabilities by hand
    mat[2][1] = 0.12  # 2-1
    mat[1][1] = 0.10  # 1-1
    mat[1][0] = 0.09  # 1-0
    mat[2][0] = 0.08  # 2-0
    mat[0][1] = 0.07  # 0-1
    mat[3][1] = 0.06  # 3-1
    mat[1][2] = 0.06  # 1-2
    mat[2][2] = 0.05  # 2-2
    mat[0][0] = 0.05  # 0-0
    mat[0][2] = 0.04  # 0-2
    mat[3][0] = 0.04  # 3-0
    mat[0][3] = 0.03  # 0-3
    mat[1][3] = 0.03  # 1-3
    mat[3][2] = 0.03  # 3-2
    mat[2][3] = 0.02  # 2-3
    # Fill remaining cells with small random values
    remaining = 1.0 - sum(sum(row) for row in mat)
    unfilled = sum(1 for h in range(g + 1) for a in range(g + 1) if mat[h][a] == 0.0)
    if unfilled > 0:
        fill = remaining / unfilled
        for h in range(g + 1):
            for a in range(g + 1):
                if mat[h][a] == 0.0:
                    mat[h][a] = fill
    return mat


@pytest.fixture
def tmp_db() -> str:
    """Create a temporary SQLite database for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    # Create the score calibration tables in the temp DB
    conn = sqlite3.connect(path)
    try:
        conn.executescript("""
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
            CREATE TABLE IF NOT EXISTS score_calibration_drift (
                bucket              TEXT PRIMARY KEY,
                match_count         INTEGER NOT NULL DEFAULT 0,
                sum_predicted_prob  REAL    NOT NULL DEFAULT 0.0,
                sum_actual          REAL    NOT NULL DEFAULT 0.0,
                calibrated_ratio    REAL    NOT NULL DEFAULT 1.0,
                last_updated        TEXT    NOT NULL DEFAULT (datetime('now'))
            );
        """)
        # Seed drift buckets
        for bn, _, _ in BUCKETS:
            conn.execute("INSERT OR IGNORE INTO score_calibration_drift (bucket) VALUES (?)", (bn,))
        conn.commit()
    finally:
        conn.close()
    yield path
    # Cleanup
    import os
    os.close(fd)
    Path(path).unlink(missing_ok=True)


# ── Bucket assignment tests ───────────────────────────────────────


class TestBucketAssignment:
    def test_total_0(self):
        assert bucket_for_total_goals(0) == "total_0"

    def test_total_1(self):
        assert bucket_for_total_goals(1) == "total_1"

    def test_total_2(self):
        assert bucket_for_total_goals(2) == "total_2"

    def test_total_3plus(self):
        assert bucket_for_total_goals(3) == "total_3plus"
        assert bucket_for_total_goals(5) == "total_3plus"
        assert bucket_for_total_goals(10) == "total_3plus"

    def test_all_buckets_have_labels(self):
        for bn, _, _ in BUCKETS:
            assert bn in BUCKET_LABELS, f"Missing label for {bn}"


# ── Bucket probability computation tests ─────────────────────────


class TestComputeBucketProbs:
    def test_uniform_matrix_sums_to_1(self):
        mat = _make_uniform_matrix(5)
        probs = compute_bucket_probs(mat)
        total = sum(probs.values())
        assert abs(total - 1.0) < 0.001, f"Sum={total}"

    def test_dirac_only_in_correct_bucket(self):
        """A Dirac at (2,1) means total=3 → should be in total_3plus bucket."""
        mat = _make_dirac_matrix(2, 1)
        probs = compute_bucket_probs(mat)
        assert abs(probs["total_3plus"] - 1.0) < 0.001
        assert abs(probs["total_0"]) < 0.001
        assert abs(probs["total_1"]) < 0.001
        assert abs(probs["total_2"]) < 0.001

    def test_dirac_0_0_in_total_0(self):
        mat = _make_dirac_matrix(0, 0)
        probs = compute_bucket_probs(mat)
        assert abs(probs["total_0"] - 1.0) < 0.001

    def test_dirac_1_0_in_total_1(self):
        mat = _make_dirac_matrix(1, 0)
        probs = compute_bucket_probs(mat)
        assert abs(probs["total_1"] - 1.0) < 0.001

    def test_dirac_1_1_in_total_2(self):
        mat = _make_dirac_matrix(1, 1)
        probs = compute_bucket_probs(mat)
        assert abs(probs["total_2"] - 1.0) < 0.001

    def test_dirac_0_2_in_total_2(self):
        mat = _make_dirac_matrix(0, 2)
        probs = compute_bucket_probs(mat)
        assert abs(probs["total_2"] - 1.0) < 0.001

    def test_realistic_matrix(self):
        mat = _make_realistic_matrix()
        probs = compute_bucket_probs(mat)
        total = sum(probs.values())
        assert abs(total - 1.0) < 0.005, f"Sum={total}"
        # 0-0 is 0.05, total_0 should be ~0.05
        assert abs(probs["total_0"] - 0.05) < 0.02

    def test_small_matrix_2x2(self):
        """2×2 matrix (max_g=1)."""
        mat = [
            [0.3, 0.3],  # h=0 → a=0: total_0, a=1: total_1
            [0.2, 0.2],  # h=1 → a=0: total_1, a=1: total_2
        ]
        probs = compute_bucket_probs(mat)
        assert abs(probs["total_0"] - 0.3) < 0.001   # (0,0)
        assert abs(probs["total_1"] - 0.5) < 0.001   # (0,1)+(1,0)
        assert abs(probs["total_2"] - 0.2) < 0.001   # (1,1)
        assert abs(probs["total_3plus"] - 0.0) < 0.001


# ── Full calibration tracking pipeline tests ────────────────────


class TestLogScoreCalibration:
    def test_first_match_creates_log_and_drift(self, tmp_db):
        mat = _make_dirac_matrix(1, 0)  # total=1
        result = log_score_calibration(
            match_id="match-1",
            home_goals=1,
            away_goals=0,
            score_matrix=mat,
            db_path=tmp_db,
        )
        # Verify result dict
        assert len(result) == 4
        assert result["total_1"]["actual"] == 1
        assert abs(result["total_1"]["predicted_prob"] - 1.0) < 0.001

        # Verify log table
        log = get_calibration_log(match_id="match-1", db_path=tmp_db)
        assert len(log) == 4
        total_1_entry = [e for e in log if e["bucket"] == "total_1"][0]
        assert total_1_entry["actual"] == 1
        assert total_1_entry["home_goals"] == 1
        assert total_1_entry["away_goals"] == 0

        # Verify drift table
        summary = get_calibration_summary(db_path=tmp_db)
        assert summary["overall_match_count"] == 1
        assert summary["buckets"]["total_1"]["match_count"] == 1
        assert abs(summary["buckets"]["total_1"]["sum_actual"] - 1.0) < 0.001

    def test_multiple_matches_accumulate_correctly(self, tmp_db):
        """Log 3 matches and verify drift ratios."""
        # Match 1: 1-0 (total=1), uniform matrix
        log_score_calibration(
            match_id="m1", home_goals=1, away_goals=0,
            score_matrix=_make_uniform_matrix(5),
            db_path=tmp_db,
        )
        # Match 2: 2-1 (total=3 → total_3plus), same uniform matrix
        log_score_calibration(
            match_id="m2", home_goals=2, away_goals=1,
            score_matrix=_make_uniform_matrix(5),
            db_path=tmp_db,
        )
        # Match 3: 0-0 (total=0), same uniform matrix
        log_score_calibration(
            match_id="m3", home_goals=0, away_goals=0,
            score_matrix=_make_uniform_matrix(5),
            db_path=tmp_db,
        )

        summary = get_calibration_summary(db_path=tmp_db)
        assert summary["overall_match_count"] == 3

        # For 5+1=6 per side, 6×6=36 cells
        # total_0: (0,0) only → 1/36 ≈ 0.0278
        # total_1: (0,1),(1,0) → 2/36 ≈ 0.0556
        # total_2: (0,2),(1,1),(2,0) → 3/36 ≈ 0.0833
        # total_3plus: 30/36 ≈ 0.8333
        # Across 3 matches, each bucket gets 3 × its p mass
        b0 = summary["buckets"]["total_0"]
        b3 = summary["buckets"]["total_3plus"]

        # total_0 predicted sum ≈ 0.0834 (3 × 0.0278), actual = 1 (m3 was 0-0)
        assert abs(b0["sum_actual"] - 1.0) < 0.01
        # Ratio should be 1.0 / (3 * 1/36) = 36/3 = 12
        assert b0["calibrated_ratio"] > 5.0  # massively underestimating 0-goal games

        # total_3plus predicted sum ≈ 2.5 (3 × 0.8333), actual = 1 (m2 was 3 total)
        assert abs(b3["sum_actual"] - 1.0) < 0.01

    def test_idempotent_rerun(self, tmp_db):
        """Re-running for the same match should replace, not duplicate."""
        mat = _make_dirac_matrix(2, 1)
        # First run
        log_score_calibration(
            match_id="same-match", home_goals=2, away_goals=1,
            score_matrix=mat, db_path=tmp_db,
        )
        # Second run with different score (simulating correction)
        log_score_calibration(
            match_id="same-match", home_goals=1, away_goals=0,
            score_matrix=mat, db_path=tmp_db,
        )

        # Should still have only 4 log rows (one per bucket)
        log = get_calibration_log(match_id="same-match", db_path=tmp_db)
        assert len(log) == 4

        # The updated entry should reflect the new score (1-0, total=1)
        total_1_entry = [e for e in log if e["bucket"] == "total_1"][0]
        assert total_1_entry["actual"] == 1
        assert total_1_entry["home_goals"] == 1
        assert total_1_entry["away_goals"] == 0

        # total_0 should now be 0 (not 1)
        total_0_entry = [e for e in log if e["bucket"] == "total_0"][0]
        assert total_0_entry["actual"] == 0

        # Drift should have match_count=1 (not 2)
        summary = get_calibration_summary(db_path=tmp_db)
        assert summary["overall_match_count"] == 1

    def test_none_matrix_returns_empty(self, tmp_db):
        result = log_score_calibration(
            match_id="no-matrix",
            home_goals=1, away_goals=0,
            score_matrix=None,
            db_path=tmp_db,
        )
        assert result == {}

    def test_empty_matrix_returns_empty(self, tmp_db):
        result = log_score_calibration(
            match_id="empty-matrix",
            home_goals=1, away_goals=0,
            score_matrix=[],
            db_path=tmp_db,
        )
        assert result == {}

    def test_goals_beyond_max_g_handled(self, tmp_db):
        """When actual goals=7 but matrix max_g=5, total=7 → bucket=total_3plus."""
        mat = _make_uniform_matrix(5)
        result = log_score_calibration(
            match_id="high-score",
            home_goals=4, away_goals=3,
            score_matrix=mat,
            db_path=tmp_db,
        )
        # Actual total = 7, bucket = total_3plus
        assert result["total_3plus"]["actual"] == 1
        assert result["total_0"]["actual"] == 0

    def test_snapshot_id_stored(self, tmp_db):
        mat = _make_uniform_matrix(5)
        log_score_calibration(
            match_id="snap-test",
            home_goals=0, away_goals=0,
            score_matrix=mat,
            snapshot_id="snap-abc-123",
            db_path=tmp_db,
        )
        log = get_calibration_log(match_id="snap-test", db_path=tmp_db)
        assert all(e["snapshot_id"] == "snap-abc-123" for e in log)


# ── Summary and drift reporting tests ────────────────────────────


class TestCalibrationSummary:
    def test_empty_db_returns_sensible_default(self, tmp_db):
        # Truncate all tables
        conn = sqlite3.connect(tmp_db)
        conn.execute("DELETE FROM score_calibration_log")
        conn.execute("DELETE FROM score_calibration_drift")
        conn.commit()
        conn.close()

        summary = get_calibration_summary(db_path=tmp_db)
        assert summary["overall_match_count"] == 0
        assert summary["max_deviation"] == 0.0
        assert summary["needs_attention"] == []

    def test_missing_db_returns_default(self):
        summary = get_calibration_summary(db_path="/nonexistent/path.db")
        assert summary["overall_match_count"] == 0

    def test_needs_attention_flags_large_deviation(self, tmp_db):
        """Log 20 matches all as 0-0 (total_0=1 every time), uniform matrix.
        The predicted mass for total_0 is 1/36 ≈ 0.028, but actual is 1.0
        → ratio ≈ 36, which should trigger needs_attention."""
        mat = _make_uniform_matrix(5)
        for i in range(20):
            log_score_calibration(
                match_id=f"all-zero-{i}",
                home_goals=0, away_goals=0,
                score_matrix=mat,
                db_path=tmp_db,
            )

        summary = get_calibration_summary(db_path=tmp_db)
        assert summary["overall_match_count"] == 20
        assert "total_0" in summary["needs_attention"]
        assert "total_3plus" in summary["needs_attention"]  # massively overestimating

    def test_well_calibrated_simulation(self, tmp_db):
        """Simulate a perfectly calibrated scenario: for each bucket, the
        actual frequency matches the predicted probability exactly."""
        mat = _make_uniform_matrix(5)
        probs = compute_bucket_probs(mat)

        # Log enough matches so that the total actual per bucket roughly
        # equals the total predicted mass. We use 36 matches (one per cell)
        # and make each cell's outcome happen once.
        g = 5
        idx = 0
        for h in range(g + 1):
            for a in range(g + 1):
                log_score_calibration(
                    match_id=f"cal-{idx}",
                    home_goals=h, away_goals=a,
                    score_matrix=mat,
                    db_path=tmp_db,
                )
                idx += 1

        summary = get_calibration_summary(db_path=tmp_db)
        # Expected total per bucket:
        # total_0: 1 match × 1/36 → 1 occurrence, predicted = 36 * 1/36 = 1
        # This is exact calibration
        for bn in probs:
            info = summary["buckets"][bn]
            # Since we simulated each cell exactly once with uniform matrix,
            # the ratio should be very close to 1.0
            assert abs(info["calibrated_ratio"] - 1.0) < 0.15, (
                f"{bn}: ratio={info['calibrated_ratio']} expected ~1.0, "
                f"pred={info['sum_predicted_prob']}, act={info['sum_actual']}"
            )


# ── Edge case tests ───────────────────────────────────────────────


class TestEdgeCases:
    def test_all_zero_matrix(self):
        """A zero matrix should still produce sensible bucket probs."""
        mat = [[0.0] * 6 for _ in range(6)]
        probs = compute_bucket_probs(mat)
        assert all(abs(p) < 0.001 for p in probs.values())

    def test_negative_probability_handled(self):
        """Negative probabilities (shouldn't happen, but be robust)."""
        mat = [[0.1] * 6 for _ in range(6)]
        mat[0][0] = -0.05  # malformed
        probs = compute_bucket_probs(mat)
        # Should still produce valid values (sum may not be 1.0)
        assert isinstance(probs["total_0"], float)

    def test_non_square_matrix(self):
        """A non-square matrix should still compute — robustness."""
        mat = [[0.2, 0.2, 0.2], [0.1, 0.1, 0.1]]
        probs = compute_bucket_probs(mat)
        assert all(isinstance(v, float) for v in probs.values())

    def test_very_large_g(self):
        """Matrix with max_g=10 should work."""
        mat = _make_uniform_matrix(10)
        probs = compute_bucket_probs(mat)
        total = sum(probs.values())
        assert abs(total - 1.0) < 0.005


# ── Ensure tables idempotency ─────────────────────────────────────


class TestEnsureTables:
    def test_default_path_uses_configured_sqlite(self, tmp_db, monkeypatch):
        from app.services import score_calibration_tracker as tracker

        monkeypatch.setattr(
            tracker,
            "current_sync_sqlite_path",
            lambda: Path(tmp_db).resolve(),
        )

        result = tracker.log_score_calibration(
            match_id="configured-db",
            home_goals=1,
            away_goals=0,
            score_matrix=_make_uniform_matrix(),
        )

        assert len(result) == 4
        assert len(tracker.get_calibration_log(match_id="configured-db")) == 4

    def test_create_twice_is_safe(self, tmp_db):
        assert ensure_tables(tmp_db) is True
        assert ensure_tables(tmp_db) is True  # second call should be fine

    def test_tables_exist_after_ensure(self, tmp_db):
        ensure_tables(tmp_db)
        conn = sqlite3.connect(tmp_db)
        try:
            cur = conn.cursor()
            for table in ["score_calibration_log", "score_calibration_drift"]:
                cur.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                )
                assert cur.fetchone()[0] == 1, f"Table {table} missing"
        finally:
            conn.close()
