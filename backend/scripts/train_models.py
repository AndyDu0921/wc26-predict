#!/usr/bin/env python3
"""Offline training that emits an immutable, unvalidated candidate bundle.

Training never replaces ``active_bundle.json``. Promotion is a separate,
audited operation after same-cohort temporal experiments pass their gates.

Usage:
    python scripts/train_models.py
    python scripts/train_models.py --team-type national --refresh
    python scripts/train_models.py --skip-weibull
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import re
import sys
import time
from datetime import datetime, timezone
from multiprocessing import Process, Queue
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import pandas as pd
import sqlite3

from app.services.dixon_coles import DixonColesModel
from app.services.tabular_match_model import TabularMatchEnhancer
from app.services.elo_ratings import EloRatingSystem
from app.services.pi_ratings import PiRatingWrapper
from app.services.weibull_model import WeibullWrapper
from app.services.artifact_bundle import sha256_file
from app.services.model_cache import CachedDC, CachedEnhancer
from app.services.sqlite_paths import current_sync_sqlite_path

# ── Paths ──
ARTIFACTS_DIR = BACKEND_DIR / "artifacts"
CANDIDATES_DIR = ARTIFACTS_DIR / "candidates"

# ═══════════════════════════════════════════════════════════════════════
#  1. Data loading
# ═══════════════════════════════════════════════════════════════════════

def load_training_data(team_type: str, refresh: bool = False) -> pd.DataFrame:
    """Load finished matches from the canonical SQLite source of truth.

    ``refresh`` remains accepted for CLI compatibility; executable dataframe
    caches are deliberately no longer read or written.
    """
    del refresh
    db_path = current_sync_sqlite_path()
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    print(f"  Loading data from SQLite ({db_path}) ...", flush=True)
    conn = sqlite3.connect(str(db_path))
    query = """
        SELECT ht.name AS home_team,
               at.name AS away_team,
               mr.home_goals,
               mr.away_goals,
               m.match_date,
               COALESCE(m.competition_weight, 1.0) AS competition_weight,
               COALESCE(m.is_neutral_venue, 0)     AS is_neutral_venue,
               m.competition,
               m.competition_type,
               m.stage,
               mr.home_xg,
               mr.away_xg
        FROM matches m
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
        JOIN match_results mr ON m.id = mr.match_id
        WHERE m.status = 'finished'
          AND (? = '' OR (ht.team_type = ? AND at.team_type = ?))
        ORDER BY m.match_date ASC
    """
    df = pd.read_sql_query(query, conn, params=(team_type, team_type, team_type))
    conn.close()

    df["match_date"] = pd.to_datetime(df["match_date"], utc=True, format="ISO8601")
    print(f"  Loaded {len(df)} matches, {df.home_team.nunique()} teams", flush=True)
    return df


def compute_fingerprint(df: pd.DataFrame) -> str:
    """Content fingerprint over the actual ordered training rows."""
    if df.empty:
        return hashlib.sha256(b"empty-training-data").hexdigest()
    columns = [
        name
        for name in (
            "home_team", "away_team", "home_goals", "away_goals",
            "match_date", "competition_weight", "is_neutral_venue",
            "competition", "competition_type", "stage", "home_xg", "away_xg",
        )
        if name in df.columns
    ]
    stable = df.loc[:, columns].copy()
    stable["match_date"] = stable["match_date"].astype(str)
    stable = stable.sort_values(columns, kind="stable").reset_index(drop=True)
    row_hashes = pd.util.hash_pandas_object(stable, index=False).values.tobytes()
    return hashlib.sha256(row_hashes).hexdigest()


# ═══════════════════════════════════════════════════════════════════════
#  2. Individual model training helpers
# ═══════════════════════════════════════════════════════════════════════

def train_dixon_coles(df: pd.DataFrame) -> tuple[DixonColesModel, float]:
    """Fit Dixon-Coles on full dataframe.  Returns (model, fit_seconds)."""
    print("  [1/5] Training Dixon-Coles ...", end=" ", flush=True)
    t0 = time.perf_counter()
    dc = DixonColesModel()
    dc.fit(df)
    elapsed = time.perf_counter() - t0
    print(f"done  [{elapsed:.1f}s]", flush=True)
    return dc, elapsed


def train_enhancer(df: pd.DataFrame) -> tuple[TabularMatchEnhancer, float]:
    """Fit TabularMatchEnhancer on full dataframe.  Returns (model, fit_seconds)."""
    print("  [2/5] Training TabularMatchEnhancer ...", end=" ", flush=True)
    t0 = time.perf_counter()
    enh = TabularMatchEnhancer()
    enh.fit(df)
    elapsed = time.perf_counter() - t0
    print(f"done  [{elapsed:.1f}s]", flush=True)
    return enh, elapsed


def train_elo(df: pd.DataFrame) -> tuple[dict[str, float], float]:
    """Fit Elo rating system.  Returns (ratings_dict, fit_seconds)."""
    print("  [3/5] Training Elo ...", end=" ", flush=True)
    t0 = time.perf_counter()
    elo = EloRatingSystem()
    elo.fit(df)
    ratings = elo.get_ratings()
    elapsed = time.perf_counter() - t0
    print(f"done  [{elapsed:.1f}s]", flush=True)
    return ratings, elapsed


def train_pi(df: pd.DataFrame) -> tuple[dict[str, float], float]:
    """Fit Pi-Rating (penaltyblog).  Returns (ratings_dict, fit_seconds)."""
    print("  [4/5] Training Pi-Rating ...", end=" ", flush=True)
    t0 = time.perf_counter()
    pi = PiRatingWrapper()
    pi.fit(df)
    ratings = pi.get_ratings_dict()
    elapsed = time.perf_counter() - t0
    print(f"done  [{elapsed:.1f}s]", flush=True)
    return ratings, elapsed


# ── Weibull subprocess isolation ──

def _weibull_worker(df_path: str, output_path: str, result_queue: "Queue[str]") -> None:
    """Run inside a child process: fit Weibull, pickle on success, signal back."""
    try:
        df = pd.read_csv(df_path, parse_dates=["match_date"])
        wb = WeibullWrapper()
        success = wb.fit(df)
        if success:
            with open(output_path, "wb") as f:
                pickle.dump(wb, f)
            result_queue.put("success")
        else:
            result_queue.put("failed:fit_returned_false")
    except Exception as exc:
        result_queue.put(f"failed:{exc}")


def train_weibull(
    df: pd.DataFrame,
    *,
    output_path: Path,
) -> tuple[bool, float, str]:
    """Fit Weibull in a separate *process* with a 120-second timeout.

    Returns (success, elapsed_seconds, status_label) where status_label
    is ``"ready"``, ``"disabled_timeout"``, or ``"failed"``.
    """
    print("  [5/5] Training Weibull (subprocess, timeout=120s) ...", end=" ", flush=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Use last 2000 matches (form-sensitive model)
    wb_df = df.sort_values("match_date").tail(2000).copy()
    temp_df_path = output_path.parent / "_weibull_temp_df.csv.gz"

    wb_df.to_csv(temp_df_path, index=False, compression="gzip")

    result_queue: Queue[str] = Queue()
    proc = Process(
        target=_weibull_worker,
        args=(str(temp_df_path), str(output_path), result_queue),
    )

    t0 = time.perf_counter()
    proc.start()
    proc.join(timeout=120)
    elapsed = time.perf_counter() - t0

    # Clean up temp file
    try:
        if temp_df_path.exists():
            temp_df_path.unlink()
    except Exception:
        pass

    if proc.is_alive():
        proc.terminate()
        proc.join()
        _try_remove(output_path)
        print(f"TIMEOUT  [{elapsed:.1f}s]", flush=True)
        return False, elapsed, "disabled_timeout"

    # --- Subprocess completed within the deadline ---
    try:
        result = result_queue.get_nowait()
    except Exception:
        result = "failed:no_result_from_subprocess"

    if result == "success":
        print(f"done  [{elapsed:.1f}s]", flush=True)
        return True, elapsed, "ready"

    # Failed — remove any artifact that was written
    _try_remove(output_path)
    print(f"FAILED  [{elapsed:.1f}s] ({result})", flush=True)
    return False, elapsed, "failed"


def _try_remove(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════
#  3. Artifact save helpers
# ═══════════════════════════════════════════════════════════════════════

def save_dc(dc: DixonColesModel, path: Path) -> None:
    cached = CachedDC(
        attack_params=dc.attack_params.copy(),
        defense_params=dc.defense_params.copy(),
        home_advantage=dc.home_advantage,
        rho=dc.rho,
        _team_order=list(dc._team_order),
        trained_at=getattr(dc, "trained_at", datetime.now(timezone.utc)),
    )
    _write_pickle(path, cached)


def save_enhancer(enh: TabularMatchEnhancer, path: Path) -> None:
    cached = CachedEnhancer(
        model=enh.model,
        feature_columns=enh.feature_columns.copy(),
        training_sample_count=enh.training_sample_count,
        fitted_at=getattr(enh, "fitted_at", datetime.now(timezone.utc)),
    )
    _write_pickle(path, cached)


def save_ratings(ratings: dict[str, float], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(ratings, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"    -> {path}  ({len(ratings)} teams)", flush=True)


def _write_pickle(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"    -> {path}", flush=True)


def _artifact_record(path: Path) -> dict[str, Any]:
    relative = path.resolve().relative_to(BACKEND_DIR.parent.resolve()).as_posix()
    return {
        "path": relative,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


# ═══════════════════════════════════════════════════════════════════════
#  4. Registry
# ═══════════════════════════════════════════════════════════════════════

def write_registry(registry: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"  Registry: {path}", flush=True)


# ═══════════════════════════════════════════════════════════════════════
#  5. Main
# ═══════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="WC26 Predict — offline training for all models",
    )
    p.add_argument(
        "--team-type",
        default="national",
        help="Team type filter (default: national)",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="Compatibility flag; training data is always reloaded from canonical SQLite",
    )
    p.add_argument(
        "--skip-weibull",
        action="store_true",
        help="Skip Weibull training entirely",
    )
    p.add_argument(
        "--bundle-id",
        default="",
        help="Optional immutable candidate bundle id.",
    )
    p.add_argument(
        "--output-root",
        default=str(CANDIDATES_DIR),
        help="Root directory for immutable candidate bundles.",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    print("=" * 60)
    print("  WC26 Predict — Offline Training")
    print(f"    Team type:       {args.team_type}")
    if args.refresh:
        print("    --refresh:       accepted (canonical SQLite is always reloaded)")
    if args.skip_weibull:
        print("    --skip-weibull:  Weibull will NOT be trained")
    print("=" * 60)
    print()

    # ── 1. Load data ────────────────────────────────────────────────
    print("[1] Loading training data")
    df = load_training_data(args.team_type, refresh=args.refresh)
    fingerprint = compute_fingerprint(df)
    training_rows = len(df)
    training_cutoff = (
        pd.Timestamp(df["match_date"].max()).isoformat()
        if not df.empty
        else "unknown"
    )
    generated_token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_id = args.bundle_id or f"candidate-{generated_token}-{fingerprint[:12]}"
    if not re.fullmatch(r"[A-Za-z0-9._-]+", bundle_id):
        raise ValueError("bundle-id may contain only letters, digits, dot, underscore, and hyphen")
    bundle_dir = (Path(args.output_root) / bundle_id).resolve()
    output_root = Path(args.output_root).resolve()
    if output_root not in bundle_dir.parents:
        raise ValueError("candidate bundle path escapes output-root")
    if bundle_dir.exists():
        raise FileExistsError(f"Candidate bundle already exists: {bundle_dir}")
    bundle_dir.mkdir(parents=True)
    artifact_paths = {
        "dixon_coles": bundle_dir / "dixon_coles.pkl",
        "tabular_enhancer": bundle_dir / "tabular_enhancer.pkl",
        "elo": bundle_dir / "elo.json",
        "pi_rating": bundle_dir / "pi_rating.json",
        "weibull": bundle_dir / "weibull.pkl",
    }
    print(f"    Fingerprint:  {fingerprint}")
    print(f"    Cutoff:       {training_cutoff}")
    print(f"    Bundle:       {bundle_id}")
    print(f"    Rows:         {training_rows}")
    print(f"    Date range:   {df['match_date'].min().date()}  ->  {df['match_date'].max().date()}")
    print(f"    Teams:        {df.home_team.nunique()}")
    print()

    # Ensure all output directories exist
    for d in (ARTIFACTS_DIR, CANDIDATES_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # ── 2. Train models ─────────────────────────────────────────────
    total_start = time.perf_counter()
    components: dict[str, dict[str, Any]] = {}

    # a. Dixon-Coles
    try:
        dc_model, dc_sec = train_dixon_coles(df)
        save_dc(dc_model, artifact_paths["dixon_coles"])
        components["dixon_coles"] = {
            "status": "ready",
            "fit_seconds": round(dc_sec, 1),
            "required_for": ["full_pipeline", "baseline_pipeline"],
        }
    except Exception as exc:
        print(f"    ** FAILED: {exc}", flush=True)
        components["dixon_coles"] = {
            "status": "failed",
            "fit_seconds": 0,
            "required_for": ["full_pipeline", "baseline_pipeline"],
            "error": str(exc)[:200],
        }

    # b. TabularMatchEnhancer
    try:
        enh_model, enh_sec = train_enhancer(df)
        save_enhancer(enh_model, artifact_paths["tabular_enhancer"])
        components["tabular_enhancer"] = {
            "status": "ready",
            "fit_seconds": round(enh_sec, 1),
            "required_for": ["full_pipeline"],
        }
    except Exception as exc:
        print(f"    ** FAILED: {exc}", flush=True)
        components["tabular_enhancer"] = {
            "status": "failed",
            "fit_seconds": 0,
            "required_for": ["full_pipeline"],
            "error": str(exc)[:200],
        }

    # c. Elo
    try:
        elo_ratings, elo_sec = train_elo(df)
        save_ratings(elo_ratings, artifact_paths["elo"])
        components["elo"] = {
            "status": "ready",
            "fit_seconds": round(elo_sec, 1),
            "required_for": ["full_pipeline"],
        }
    except Exception as exc:
        print(f"    ** FAILED: {exc}", flush=True)
        components["elo"] = {
            "status": "failed",
            "fit_seconds": 0,
            "required_for": ["full_pipeline"],
            "error": str(exc)[:200],
        }

    # d. Pi-Rating
    try:
        pi_ratings, pi_sec = train_pi(df)
        save_ratings(pi_ratings, artifact_paths["pi_rating"])
        components["pi_rating"] = {
            "status": "ready",
            "fit_seconds": round(pi_sec, 1),
            "required_for": ["full_pipeline"],
        }
    except Exception as exc:
        print(f"    ** FAILED: {exc}", flush=True)
        components["pi_rating"] = {
            "status": "failed",
            "fit_seconds": 0,
            "required_for": ["full_pipeline"],
            "error": str(exc)[:200],
        }

    # e. Weibull (optional)
    if args.skip_weibull:
        print("  [5/5] Skipping Weibull (--skip-weibull)", flush=True)
        components["weibull"] = {
            "status": "skipped",
            "fit_seconds": 0,
            "required_for": ["full_pipeline"],
        }
    else:
        try:
            wb_ok, wb_sec, wb_status = train_weibull(
                df,
                output_path=artifact_paths["weibull"],
            )
            components["weibull"] = {
                "status": wb_status,
                "fit_seconds": round(wb_sec, 1),
                "required_for": ["full_pipeline"],
            }
        except Exception as exc:
            print(f"    ** FAILED: {exc}", flush=True)
            components["weibull"] = {
                "status": "failed",
                "fit_seconds": 0,
                "required_for": ["full_pipeline"],
                "error": str(exc)[:200],
            }

    total_elapsed = time.perf_counter() - total_start

    # ── 3. Write registry ──────────────────────────────────────────
    registry: dict[str, Any] = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "data_fingerprint": fingerprint,
        "training_rows": training_rows,
        "team_type": args.team_type,
        "total_seconds": round(total_elapsed, 1),
        "components": components,
    }
    registry_path = bundle_dir / "training_registry.json"
    write_registry(registry, registry_path)
    bundle_components = {
        name: _artifact_record(path)
        for name, path in artifact_paths.items()
        if path.is_file() and components.get(name, {}).get("status") == "ready"
    }
    bundle_manifest = {
        "schema_version": "model_artifact_bundle.v1",
        "bundle_id": bundle_id,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "status": "candidate_unvalidated",
        "promotion_evidence": False,
        "training_data": {
            "cutoff": training_cutoff,
            "fingerprint": fingerprint,
            "row_count": training_rows,
            "team_type": args.team_type,
            "provenance_complete": training_cutoff != "unknown",
        },
        "components": bundle_components,
        "training_registry": _artifact_record(registry_path),
        "notes": (
            "Immutable training output only. It is not active and cannot be "
            "promoted without same-cohort temporal experiment evidence."
        ),
    }
    manifest_path = bundle_dir / "bundle.json"
    manifest_path.write_text(
        json.dumps(bundle_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # ── 4. Print summary ────────────────────────────────────────────
    _print_summary(components, total_elapsed, manifest_path=manifest_path)


def _print_summary(
    components: dict[str, dict[str, Any]],
    total_elapsed: float,
    *,
    manifest_path: Path,
) -> None:
    labels = {
        "dixon_coles": "Dixon-Coles",
        "tabular_enhancer": "TabularEnhancer",
        "elo": "Elo",
        "pi_rating": "Pi-Rating",
        "weibull": "Weibull",
    }

    print()
    print("TRAINING COMPLETE")
    for key, label in labels.items():
        info = components.get(key, {})
        status = info.get("status", "unknown")
        seconds = info.get("fit_seconds", 0)

        if status == "ready":
            status_fmt = "ready"
        elif status == "disabled_timeout":
            status_fmt = "TIMEOUT (disabled)"
        elif status == "failed":
            err = info.get("error", "")
            status_fmt = f"FAILED  {err[:60]}"
        elif status == "skipped":
            status_fmt = "skipped"
        else:
            status_fmt = status

        status_fmt = status_fmt.replace("\\n", " ")
        print(f"  {label:<20} {seconds:>6.1f}s ({status_fmt})")

    print(f"  {'Total':<20} {total_elapsed:>6.1f}s")
    print(f"  Candidate manifest: {manifest_path}")
    print("  Active bundle: unchanged")
    print()


if __name__ == "__main__":
    main()
