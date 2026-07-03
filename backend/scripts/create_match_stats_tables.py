"""
V4.6-process-eval: Create match process statistics tables.

Three new tables:
  match_statistics_raw   — raw provider JSON payloads (audit trail)
  match_team_statistics  — cleaned per-team per-match stats
  postmatch_process_eval — process evaluation (predicted vs actual)

Usage:
  python backend/scripts/create_match_stats_tables.py [--db-path PATH]
"""

import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "local_stage2.db"

DDL = """
-- ============================================================
-- Table 1: Raw provider payload (immutable audit trail)
-- ============================================================
CREATE TABLE IF NOT EXISTS match_statistics_raw (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id        INTEGER NOT NULL,
    provider        TEXT    NOT NULL,
    provider_match_id TEXT,
    source_url      TEXT,
    fetched_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    payload_json    TEXT    NOT NULL,
    payload_hash    TEXT,
    status          TEXT    DEFAULT 'fetched',
    notes           TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),

    FOREIGN KEY (match_id) REFERENCES wc26_schedule(id)
);

CREATE INDEX IF NOT EXISTS idx_msr_match ON match_statistics_raw(match_id);
CREATE INDEX IF NOT EXISTS idx_msr_provider ON match_statistics_raw(provider);

-- ============================================================
-- Table 2: Cleaned per-team per-match stats
-- ============================================================
CREATE TABLE IF NOT EXISTS match_team_statistics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id        INTEGER NOT NULL,
    team_name       TEXT    NOT NULL,
    side            TEXT    CHECK (side IN ('home', 'away')),
    -- Offensive output
    goals           INTEGER,
    xg              REAL,
    shots_total     INTEGER,
    shots_on_target INTEGER,
    shots_inside_box INTEGER,
    big_chances     INTEGER,
    corners         INTEGER,
    -- Possession & passing
    possession_pct  REAL,
    passes_attempted INTEGER,
    pass_accuracy_pct REAL,
    final_third_entries INTEGER,
    -- Defensive actions
    tackles         INTEGER,
    interceptions   INTEGER,
    clearances      INTEGER,
    fouls           INTEGER,
    yellow_cards    INTEGER,
    red_cards       INTEGER,
    -- Goalkeeper
    saves           INTEGER,
    -- Special events
    penalties_awarded INTEGER DEFAULT 0,
    penalties_scored  INTEGER DEFAULT 0,
    own_goals         INTEGER DEFAULT 0,
    -- Data lineage
    provider        TEXT    NOT NULL,
    data_quality_score REAL DEFAULT 0.0,
    is_primary      INTEGER DEFAULT 0,
    conflict_flag   INTEGER DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),

    FOREIGN KEY (match_id) REFERENCES wc26_schedule(id),
    UNIQUE(match_id, team_name, provider)
);

CREATE INDEX IF NOT EXISTS idx_mts_match ON match_team_statistics(match_id);
CREATE INDEX IF NOT EXISTS idx_mts_team  ON match_team_statistics(team_name);

-- ============================================================
-- Table 3: Process evaluation (predicted vs actual)
-- ============================================================
CREATE TABLE IF NOT EXISTS postmatch_process_eval (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id            INTEGER NOT NULL,
    -- Predicted xG (from pre_match_snapshots or DC model)
    predicted_home_xg   REAL,
    predicted_away_xg   REAL,
    -- Actual stats (from match_team_statistics)
    actual_home_xg      REAL,
    actual_away_xg      REAL,
    actual_home_goals   INTEGER,
    actual_away_goals   INTEGER,
    -- xG error metrics
    xg_home_error       REAL,
    xg_away_error       REAL,
    xg_mae              REAL,
    xg_direction_correct INTEGER,  -- 1 if predicted xG winner == actual xG winner
    -- Total goals
    predicted_total_goals REAL,
    actual_total_xg     REAL,
    total_xg_error      REAL,
    -- Finishing (goals - xG)
    finishing_delta_home REAL,
    finishing_delta_away REAL,
    -- Shot volume
    shot_volume_delta_home REAL,
    shot_volume_delta_away REAL,
    -- Dominance index (0-1 scale)
    dominance_index_home REAL,
    dominance_index_away REAL,
    -- Classification
    process_winner      TEXT,    -- which team dominated the process
    outcome_correct     INTEGER, -- 1 if final result prediction was correct
    process_correct     INTEGER, -- 1 if process (xG direction) matches prediction
    xg_result_alignment TEXT,    -- 'aligned' / 'contradicted' / 'unclear'
    process_label       TEXT,    -- 'PROCESS_SUPPORTED' / 'PROCESS_CONTRADICTED' / 'PROCESS_UNCLEAR'
    model_failure_type  TEXT,    -- failure taxonomy label
    learning_weight     REAL DEFAULT 0.0,
    recommended_action  TEXT,
    notes               TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),

    FOREIGN KEY (match_id) REFERENCES wc26_schedule(id),
    UNIQUE(match_id)
);

CREATE INDEX IF NOT EXISTS idx_ppe_match ON postmatch_process_eval(match_id);
CREATE INDEX IF NOT EXISTS idx_ppe_failure ON postmatch_process_eval(model_failure_type);
"""


def create_tables(db_path: str = None) -> bool:
    path = Path(db_path) if db_path else DEFAULT_DB
    if not path.exists():
        print(f"ERROR: Database not found at {path}")
        return False

    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(DDL)
        conn.commit()
        print(f"Created 3 tables in {path}")
        # Verify
        cur = conn.cursor()
        for table in ["match_statistics_raw", "match_team_statistics", "postmatch_process_eval"]:
            cur.execute(f"SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='{table}'")
            exists = cur.fetchone()[0]
            print(f"  {table}: {'OK' if exists else 'MISSING!'}")
    except Exception as e:
        print(f"ERROR: {e}")
        return False
    finally:
        conn.close()
    return True


if __name__ == "__main__":
    # Parse args safely — avoid IndexError with bare argv access
    db_path = None
    if len(sys.argv) == 3 and sys.argv[1] == "--db-path":
        db_path = sys.argv[2]
    elif len(sys.argv) > 1:
        print(f"Usage: python {Path(__file__).name} [--db-path PATH]")
        print(f"  Default DB: {DEFAULT_DB}")
        sys.exit(1)
    ok = create_tables(db_path)
    sys.exit(0 if ok else 1)
