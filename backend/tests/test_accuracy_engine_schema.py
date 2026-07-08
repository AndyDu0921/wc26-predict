import sqlite3
from pathlib import Path

import pytest


def test_local_accuracy_engine_tables_exist_after_migration():
    db_path = Path(__file__).resolve().parents[1] / "data" / "local_stage2.db"
    if not db_path.exists():
        pytest.skip("local DB unavailable")
    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        prediction_snapshot_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(prediction_snapshots)")
        }
    finally:
        conn.close()

    assert version == "h8c9d0e1f2g3"
    assert {
        "feature_snapshots",
        "experiment_runs",
        "candidate_predictions",
        "model_change_proposals",
        "evidence_items",
        "information_state_signals",
        "signal_evaluations",
        "match_data_raw",
        "match_events",
        "shot_events",
        "match_lineups",
        "player_match_minutes",
        "match_player_statistics",
        "match_game_state_segments",
    }.issubset(tables)
    assert {"fused_score_matrix", "source_score_matrices"}.issubset(prediction_snapshot_columns)
