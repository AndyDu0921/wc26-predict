"""add_pre_match_snapshot_v1 — full-contract pre_match_snapshot table

Revision ID: c1e2f3a4b5c6
Revises: b8b7d1a21f6b
Create Date: 2026-06-09 18:30:00.000000

Adds the immutable pre_match_snapshot table with:
- input_hash (SHA256, tamper-evident)
- git_commit alongside code_version
- source_timestamps (JSON, per-source fetch times)
- odds_snapshot_id, weather_snapshot_id, injury_snapshot_id (reference IDs)
- freeze_time = snapshot_at (property, no extra column needed)
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c1e2f3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'b8b7d1a21f6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The table may already exist from _ensure_table() in snapshot_service.py.
    # If not, create it; if yes (via raw sqlite3), Alembic tracks the schema.
    op.execute("""
        CREATE TABLE IF NOT EXISTS pre_match_snapshots (
            id TEXT PRIMARY KEY,
            match_id TEXT,
            snapshot_at TEXT,
            kickoff_at TEXT,
            hours_to_kickoff REAL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            competition TEXT NOT NULL,
            is_neutral INTEGER DEFAULT 0,
            weather_available INTEGER DEFAULT 0,
            odds_available INTEGER DEFAULT 0,
            lineup_available INTEGER DEFAULT 0,
            injury_data_available INTEGER DEFAULT 0,
            news_signals_available INTEGER DEFAULT 0,
            weather_snapshot TEXT,
            odds_snapshot TEXT,
            lineup_snapshot TEXT,
            injury_records TEXT,
            news_signal_ids TEXT,
            component_probs TEXT,
            final_home_prob REAL NOT NULL,
            final_draw_prob REAL NOT NULL,
            final_away_prob REAL NOT NULL,
            home_xg REAL,
            away_xg REAL,
            top_scores TEXT,
            weight_config_label TEXT,
            weight_config TEXT,
            effective_weights TEXT,
            fusion_graph TEXT,
            model_disagreement REAL,
            market_blended INTEGER DEFAULT 0,
            market_weight_used REAL,
            market_divergence REAL,
            confidence TEXT,
            confidence_penalty REAL,
            risk_tags TEXT,
            pipeline_status TEXT,
            missing_inputs TEXT,
            degraded_reasons TEXT,
            code_version TEXT NOT NULL,
            model_version TEXT,
            data_fingerprint TEXT,
            git_commit TEXT,
            input_hash TEXT,
            source_timestamps TEXT,
            odds_snapshot_id TEXT,
            weather_snapshot_id TEXT,
            injury_snapshot_id TEXT,
            prediction_mode TEXT DEFAULT 'full',
            report_markdown TEXT,
            llm_analysis TEXT
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pre_match_snapshots")
