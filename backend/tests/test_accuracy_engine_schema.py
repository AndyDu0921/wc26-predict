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
    finally:
        conn.close()

    assert version == "e5f6a7b8c9d0"
    assert {
        "feature_snapshots",
        "experiment_runs",
        "candidate_predictions",
        "model_change_proposals",
    }.issubset(tables)
