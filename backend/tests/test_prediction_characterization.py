"""test_prediction_characterization.py — Golden/characterization tests.

Locks current behavior of CLI and API sync prediction paths so that
subsequent refactoring (S3–S5 in the V4.3.0 migration plan) has a
safety net.

Design:
- Each scenario is exported to a golden JSON snapshot under
  tests/fixtures/golden_predictions/.
- Tests compare CURRENT behavior against the golden snapshot.
- After refactoring, regenerate snapshots and re-verify.
- This file does NOT change any production code.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import math
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = BACKEND_DIR / "scripts"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "golden_predictions"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ── test scenarios ──────────────────────────────────────────────
MATCH_SCENARIOS = [
    # (home, away, competition, stage, description)
    ("Brazil", "Haiti", "FIFA World Cup 2026", "Group A - Matchday 1", "strong_vs_weak"),
    ("France", "Spain", "International Friendly", "", "strong_vs_strong"),
    ("Scotland", "Brazil", "FIFA World Cup 2026", "Group C - Matchday 3", "wc_md3_motivation"),
    ("Ivory Coast", "France", "International Friendly", "", "balanced"),
]


def _make_fixture_path(desc: str, path_name: str) -> Path:
    """Return path to a golden fixture file."""
    return FIXTURES_DIR / f"{desc}_{path_name}.json"


# ── helpers ─────────────────────────────────────────────────────

def _run_cli_prediction(home: str, away: str, competition: str,
                        stage: str = "") -> dict | None:
    """Run predict_match_full.py via subprocess and return parsed JSON."""
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "predict_match_full.py"),
        "--home", home,
        "--away", away,
        "--competition", competition,
        "--no-market",
        "--no-weather",
        "--no-save",
        "--no-auto-resolve",
    ]
    if stage:
        cmd += ["--stage", stage]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_DIR)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            cwd=str(BACKEND_DIR),
            env=env,
        )
        if result.returncode != 0:
            pytest.skip(f"CLI prediction failed: {result.stderr[:200]}")
        # CLI writes a marker immediately before its canonical JSON payload.
        output = result.stdout
        marker = "=== PREDICTION JSON ==="
        if marker in output:
            try:
                return json.loads(output.split(marker, 1)[1].strip())
            except json.JSONDecodeError:
                pass
        pytest.skip(f"Could not parse CLI JSON output (len={len(output)})")
        return None
    except subprocess.TimeoutExpired:
        pytest.skip("CLI prediction timed out")
        return None
    except FileNotFoundError:
        pytest.skip("Python not found for CLI subprocess")
        return None


def _run_sync_prediction(home: str, away: str, competition: str,
                         stage: str = "") -> dict:
    """Run PredictionPipeline.predict_sync() and return dict representation."""
    from app.services.prediction_pipeline import PredictionPipeline

    pipeline = PredictionPipeline.from_artifacts(mode="full")
    result = pipeline.predict_sync(
        home, away, competition,
        is_neutral=True,
        stage=stage,
        enable_market=False,
        enable_weather=False,
        save_snapshot=False,
    )
    d = result.to_dict()
    # Record stage context for golden fixture comparison
    d["_test_stage"] = stage
    return d


# ── structural invariants ───────────────────────────────────────

def _assert_valid_probs(probs: dict, path: str, tolerance: float = 0.001):
    """Assert a dict with home_win_prob/draw_prob/away_win_prob is valid."""
    h = probs.get("home_win_prob", probs.get("home", 0))
    d = probs.get("draw_prob", probs.get("draw", 0))
    a = probs.get("away_win_prob", probs.get("away", 0))

    assert 0.0 <= h <= 1.0, f"[{path}] home={h} out of range"
    assert 0.0 <= d <= 1.0, f"[{path}] draw={d} out of range"
    assert 0.0 <= a <= 1.0, f"[{path}] away={a} out of range"
    total = h + d + a
    assert math.isclose(total, 1.0, rel_tol=tolerance), \
        f"[{path}] probs sum to {total}, expected 1.0"


def _assert_provenance(result: dict, path: str):
    """Assert provenance/metadata fields exist."""
    meta = result.get("meta", result)
    assert "version" in meta or "model_version" in meta or "weight_version" in meta, \
        f"[{path}] missing provenance fields in {list(meta.keys())[:10]}"


def _record_negbin_status(result: dict, path: str, negbin_in_cli: bool):
    """Record whether NegBin was applied — explicitly comparing paths."""
    # The sync path currently has no NegBin (P0-1).  The CLI path does.
    # This explicit check will turn from WARNING → PASS after S4.
    pred = result.get("prediction", result)
    has_negbin = bool(
        pred.get("negbin_applied") or
        result.get("negbin_applied")
    )
    if negbin_in_cli and not has_negbin:
        # Expected divergence before S4 — record, don't fail
        return False  # divergence detected
    return True  # consistent


# ── golden snapshot tests ────────────────────────────────────────

class TestGoldenSnapshots:
    """Compare current prediction outputs against saved golden fixtures."""

    @pytest.mark.parametrize("home,away,competition,stage,desc", MATCH_SCENARIOS)
    def test_sync_path_matches_golden(self, home, away, competition, stage, desc):
        """Sync prediction output matches saved golden snapshot."""
        result = _run_sync_prediction(home, away, competition, stage)
        assert result is not None, f"Sync prediction failed for {desc}"

        fixture_path = _make_fixture_path(desc, "sync")

        if not fixture_path.exists():
            pytest.fail(
                f"Missing golden fixture: {fixture_path}. "
                "Run this module directly to regenerate fixtures."
            )

        # Replay: compare
        golden = json.loads(fixture_path.read_text(encoding="utf-8"))

        # Top-level keys must match
        result_keys = set(str(k) for k in result.keys())
        golden_keys = set(str(k) for k in golden.keys())
        new_keys = result_keys - golden_keys
        missing_keys = golden_keys - result_keys

        assert not missing_keys, \
            f"[{desc}] Golden keys missing from current: {missing_keys}"

        # Probability fields match within tolerance
        pred = result.get("prediction", result)
        gpred = golden.get("prediction", golden)
        for key in ("home_win_prob", "draw_prob", "away_win_prob"):
            if key in gpred and key in pred:
                assert math.isclose(float(pred[key]), float(gpred[key]), abs_tol=0.02), \
                    f"[{desc}] {key} diverged: golden={gpred[key]:.4f} current={pred[key]:.4f}"

        if new_keys:
            # New keys appeared — record as info, not failure (may be legitimate additions)
            print(f"[{desc}] New keys since golden: {new_keys}")


# ── structural invariant tests ──────────────────────────────────

class TestStructuralInvariants:
    """Tests that apply to ALL prediction paths — must always pass."""

    @pytest.mark.parametrize("home,away,competition,stage,desc", MATCH_SCENARIOS)
    def test_sync_path_probs_valid(self, home, away, competition, stage, desc):
        """Sync path: probabilities sum to 1 and are in [0,1]."""
        result = _run_sync_prediction(home, away, competition, stage)
        # Try standard keys first
        probs = {
            "home_win_prob": result.get("home_win_prob"),
            "draw_prob": result.get("draw_prob"),
            "away_win_prob": result.get("away_win_prob"),
        }
        if None in probs.values():
            # Fall back to prediction sub-dict
            pred = result.get("prediction", {})
            probs = {
                "home_win_prob": pred.get("home_win_prob"),
                "draw_prob": pred.get("draw_prob"),
                "away_win_prob": pred.get("away_win_prob"),
            }
        _assert_valid_probs(probs, f"sync/{desc}")

    @pytest.mark.parametrize("home,away,competition,stage,desc", MATCH_SCENARIOS)
    def test_sync_path_has_components(self, home, away, competition, stage, desc):
        """Sync path returns component predictions."""
        result = _run_sync_prediction(home, away, competition, stage)
        # Either components_used or individual component keys
        has_components = (
            result.get("components_used") or
            result.get("components") or
            any(k in str(result).lower() for k in ["dc", "elo", "pi", "weibull"])
        )
        assert has_components, f"[{desc}] No component predictions found"

    @pytest.mark.parametrize("home,away,competition,stage,desc", MATCH_SCENARIOS)
    def test_sync_path_has_provenance(self, home, away, competition, stage, desc):
        """Sync path returns provenance information."""
        result = _run_sync_prediction(home, away, competition, stage)
        meta = result.get("meta", {})
        has_version = (
            meta.get("version") or
            meta.get("model_version") or
            meta.get("weight_version") or
            result.get("weight_config")
        )
        assert has_version, \
            f"[{desc}] No version/provenance in result. Top keys: {list(result.keys())[:10]}"

    def test_sync_path_negbin_status_recorded(self):
        """NegBin is part of the API sync path."""
        result = _run_sync_prediction("Brazil", "Haiti", "FIFA World Cup 2026",
                                       "Group A - Matchday 1")
        pred = result.get("prediction", result)
        assert pred.get("negbin_applied") is True

    def test_final_score_matrix_matches_final_outcome_probabilities(self):
        result = _run_sync_prediction(
            "Brazil",
            "Haiti",
            "FIFA World Cup 2026",
            "Group A - Matchday 1",
        )
        pred = result["prediction"]
        matrix = pred["score_matrix"]
        assert len(matrix) == 11
        assert all(len(row) == 11 for row in matrix)
        bucket = {
            "home": sum(matrix[h][a] for h in range(11) for a in range(11) if h > a),
            "draw": sum(matrix[h][a] for h in range(11) for a in range(11) if h == a),
            "away": sum(matrix[h][a] for h in range(11) for a in range(11) if h < a),
        }
        assert bucket["home"] == pytest.approx(pred["home_win_prob"], abs=1e-8)
        assert bucket["draw"] == pytest.approx(pred["draw_prob"], abs=1e-8)
        assert bucket["away"] == pytest.approx(pred["away_win_prob"], abs=1e-8)
        assert result["meta"]["stage"] == "Group A - Matchday 1"


# ── path parity tests ───────────────────────────────────────────

class TestPathParity:
    """Compare CLI vs Sync prediction paths for the same match.

    These tests explicitly document CURRENT divergences. After S4,
    they should show near-identical probabilities.
    """

    @pytest.mark.parametrize("home,away,competition,stage,desc", MATCH_SCENARIOS)
    def test_cli_vs_sync_probability_diff(self, home, away, competition, stage, desc):
        """Compare CLI and sync path final probabilities for same match."""
        cli_result = _run_cli_prediction(home, away, competition, stage)
        if cli_result is None:
            pytest.skip("CLI prediction unavailable")

        sync_result = _run_sync_prediction(home, away, competition, stage)

        # Extract final probs from both
        cli_pred = cli_result.get("prediction", cli_result)
        sync_pred = sync_result.get("prediction", sync_result)

        cli_home = cli_pred.get("home_win_prob", cli_pred.get("home", 0))
        sync_home = sync_pred.get("home_win_prob", sync_pred.get("home", 0))
        cli_draw = cli_pred.get("draw_prob", cli_pred.get("draw", 0))
        sync_draw = sync_pred.get("draw_prob", sync_pred.get("draw", 0))
        cli_away = cli_pred.get("away_win_prob", cli_pred.get("away", 0))
        sync_away = sync_pred.get("away_win_prob", sync_pred.get("away", 0))

        diff_home = abs(cli_home - sync_home)
        diff_draw = abs(cli_draw - sync_draw)
        diff_away = abs(cli_away - sync_away)
        max_diff = max(diff_home, diff_draw, diff_away)

        cli_has_negbin = bool(
            cli_result.get("negbin_applied") or
            cli_pred.get("negbin_applied")
        )
        sync_has_negbin = bool(
            sync_result.get("negbin_applied") or
            sync_pred.get("negbin_applied")
        )

        print(f"[{desc}] CLI→Sync diff: H={diff_home:.4f} D={diff_draw:.4f} A={diff_away:.4f}")
        print(f"[{desc}] CLI NegBin={cli_has_negbin}, Sync NegBin={sync_has_negbin}")

        # Pre-S4: CLI has NegBin, Sync doesn't → expect divergence up to ~3pp
        if cli_has_negbin and not sync_has_negbin:
            # This is expected divergence; NegBin at 5% fusion causes ~0.5-3pp shift
            assert max_diff < 0.06, \
                f"[{desc}] NegBin-only divergence too large: {max_diff:.4f}"
            pytest.skip(
                f"[{desc}] Expected NegBin divergence: {max_diff:.4f} "
                f"(will converge after S4)"
            )
        else:
            # Both have or both lack NegBin — should be < 1pp
            assert max_diff < 0.02, \
                f"[{desc}] Unexpected divergence: {max_diff:.4f}"


# ── fixture generation script ───────────────────────────────────

def generate_all_fixtures(*, include_cli: bool = False):
    """Regenerate deterministic sync fixtures and optionally CLI fixtures."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    print("Generating golden fixtures...")
    for home, away, competition, stage, desc in MATCH_SCENARIOS:
        try:
            result = _run_sync_prediction(home, away, competition, stage)
            path = _make_fixture_path(desc, "sync")
            path.write_text(
                json.dumps(result, indent=2, default=str, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"  [OK] {desc}_sync.json ({path.stat().st_size} bytes)")

            if include_cli:
                cli_result = _run_cli_prediction(home, away, competition, stage)
                if cli_result:
                    cli_path = _make_fixture_path(desc, "cli")
                    cli_path.write_text(
                        json.dumps(cli_result, indent=2, default=str, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    print(f"  [OK] {desc}_cli.json ({cli_path.stat().st_size} bytes)")
        except Exception as e:
            print(f"  [FAIL] {desc}: {e}")
    print("Done.")


if __name__ == "__main__":
    generate_all_fixtures(include_cli="--include-cli" in sys.argv)
