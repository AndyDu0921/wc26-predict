from __future__ import annotations

import sqlite3

from app.services.signal_adjuster_sync import (
    apply_signal_adjustments,
    load_approved_signals,
)


def _signal_db(path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE teams (id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE evidence_items (id TEXT PRIMARY KEY, available_at TEXT);
            CREATE TABLE news_signals (
                id TEXT PRIMARY KEY,
                team_id TEXT,
                signal_type TEXT,
                impact_direction TEXT,
                confidence REAL,
                summary_zh TEXT,
                player_name TEXT,
                claim TEXT,
                source_reliability REAL,
                review_status TEXT,
                enters_model INTEGER,
                evidence_id TEXT,
                created_at TEXT,
                reviewed_at TEXT,
                effective_until TEXT,
                conflict_group_id TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO teams VALUES (?, ?)",
            [("home", "Home"), ("away", "Away")],
        )
        conn.executemany(
            "INSERT INTO evidence_items VALUES (?, ?)",
            [
                ("good", "2026-07-01T08:00:00+00:00"),
                ("future-evidence", "2026-07-01T11:00:00+00:00"),
                ("future-review", "2026-07-01T08:00:00+00:00"),
                ("expired", "2026-07-01T08:00:00+00:00"),
            ],
        )
        rows = [
            ("good", "good", "2026-07-01T09:00:00+00:00", "2026-07-03T00:00:00+00:00"),
            (
                "future-evidence",
                "future-evidence",
                "2026-07-01T09:00:00+00:00",
                "2026-07-03T00:00:00+00:00",
            ),
            (
                "future-review",
                "future-review",
                "2026-07-01T11:00:00+00:00",
                "2026-07-03T00:00:00+00:00",
            ),
            ("expired", "expired", "2026-07-01T09:00:00+00:00", "2026-07-01T09:30:00+00:00"),
        ]
        for signal_id, evidence_id, reviewed_at, effective_until in rows:
            conn.execute(
                """
                INSERT INTO news_signals VALUES (
                    ?, 'home', 'injury', 'negative', 0.9, 'summary', NULL,
                    'claim', 0.9, 'approved', 1, ?, '2026-07-01T08:30:00+00:00',
                    ?, ?, 'wc26:match-1'
                )
                """,
                (signal_id, evidence_id, reviewed_at, effective_until),
            )


def test_signal_loader_enforces_database_and_time_boundaries(tmp_path):
    db_path = tmp_path / "signals.db"
    _signal_db(db_path)

    signals = load_approved_signals(
        "Home",
        "Away",
        match_id="match-1",
        as_of_time="2026-07-01T10:00:00+00:00",
        kickoff_at="2026-07-02T10:00:00+00:00",
        db_path=db_path,
    )

    assert [signal["id"] for signal in signals] == ["good"]


def test_combined_negative_signal_adjustment_is_bounded():
    signals = [
        {
            "team_name": "Home",
            "impact_direction": "negative",
            "signal_type": "injury",
            "confidence": 1.0,
            "source_reliability": 1.0,
        }
        for _ in range(20)
    ]

    home, draw, away, _ = apply_signal_adjustments(
        home_prob=0.50,
        draw_prob=0.25,
        away_prob=0.25,
        home_team="Home",
        away_team="Away",
        signals=signals,
    )

    assert home >= 0.39
    assert abs(home + draw + away - 1.0) < 1e-12
