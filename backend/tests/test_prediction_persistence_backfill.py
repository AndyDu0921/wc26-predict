from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = BACKEND_DIR / "scripts" / "backfill_prediction_persistence.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("backfill_prediction_persistence", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _make_conn(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE pre_match_snapshots (
            id TEXT, match_id TEXT, snapshot_at TEXT, kickoff_at TEXT,
            home_team TEXT, away_team TEXT, competition TEXT,
            final_home_prob REAL, final_draw_prob REAL, final_away_prob REAL,
            home_xg REAL, away_xg REAL, model_version TEXT, code_version TEXT,
            prediction_mode TEXT, odds_snapshot TEXT, top_scores TEXT,
            news_signal_ids TEXT, risk_tags TEXT, component_probs TEXT,
            missing_inputs TEXT, source_score_matrices TEXT, fused_score_matrix TEXT,
            weight_config_label TEXT, weight_config TEXT, effective_weights TEXT,
            fusion_graph TEXT, model_disagreement TEXT, market_blended INTEGER,
            market_weight_used REAL, market_divergence REAL, confidence_penalty REAL,
            pipeline_status TEXT, degraded_reasons TEXT, data_fingerprint TEXT,
            source_timestamps TEXT, odds_snapshot_id TEXT, weather_snapshot_id TEXT,
            injury_snapshot_id TEXT, confidence TEXT, weather_available INTEGER,
            odds_available INTEGER, lineup_available INTEGER, injury_data_available INTEGER,
            news_signals_available INTEGER
        );
        CREATE TABLE prediction_snapshots (
            id TEXT, match_id TEXT, generated_at TEXT, model_version TEXT,
            run_type TEXT, home_team TEXT, away_team TEXT, competition TEXT,
            match_time TEXT, baseline_probs TEXT, market_probs TEXT, adjusted_probs TEXT,
            expected_goals TEXT, top_scores TEXT, elo_ratings TEXT, active_event_ids TEXT,
            missing_inputs TEXT, confidence TEXT, calibration_monitor TEXT,
            pipeline_params TEXT, report_path TEXT, report_markdown TEXT,
            component_probs TEXT
        );
        CREATE TABLE prediction_runs (
            id TEXT, match_id TEXT, run_type TEXT, model_version TEXT, as_of_time TEXT,
            home_win_prob REAL, draw_prob REAL, away_win_prob REAL, home_xg REAL,
            away_xg REAL, score_matrix TEXT, top3_scores TEXT, confidence_score REAL,
            risk_tags TEXT, input_feature_snapshot TEXT, approved_signals TEXT,
            created_at TEXT
        );
        CREATE TABLE prediction_learning_log (
            id TEXT, match_id TEXT, prediction_run_id TEXT, snapshot_id TEXT
        );
        CREATE TABLE postmatch_eval (
            id TEXT, prediction_run_id TEXT, actual_home_goals INTEGER,
            actual_away_goals INTEGER, actual_result TEXT, brier_score REAL,
            log_loss REAL, exact_score_hit INTEGER, top3_hit INTEGER,
            calibration_bucket INTEGER, notes TEXT, created_at TEXT
        );
        CREATE TABLE wc26_schedule (
            id TEXT, home_team TEXT, away_team TEXT, home_goals INTEGER,
            away_goals INTEGER, match_status TEXT
        );
        CREATE TABLE teams (id TEXT, name TEXT);
        CREATE TABLE news_signals (
            id TEXT, team_id TEXT, signal_type TEXT, impact_direction TEXT,
            evidence_id TEXT, confidence REAL, summary_zh TEXT
        );
        """
    )
    return conn


def _insert_snapshot(conn: sqlite3.Connection, match_id: str = "197") -> None:
    payload = {
        "id": f"pre-{match_id}",
        "match_id": match_id,
        "snapshot_at": "2026-07-06T06:00:00+00:00",
        "kickoff_at": "2026-07-07T03:00:00+00:00",
        "home_team": "Brazil",
        "away_team": "Norway",
        "competition": "FIFA World Cup 2026",
        "final_home_prob": 0.40,
        "final_draw_prob": 0.20,
        "final_away_prob": 0.40,
        "home_xg": 1.1,
        "away_xg": 1.4,
        "model_version": None,
        "code_version": "4.9.0-alpha",
        "prediction_mode": "full",
        "odds_snapshot": json.dumps({"home_prob": 0.35, "draw_prob": 0.25, "away_prob": 0.40}),
        "top_scores": json.dumps([{"score": "1:2", "prob": 0.12}]),
        "news_signal_ids": json.dumps(["sig-1"]),
        "risk_tags": json.dumps(["market_divergence"]),
        "component_probs": json.dumps(
            {
                "dixon_coles": {"home": 0.42, "draw": 0.20, "away": 0.38},
                "pi_rating": {"home": 0.30, "draw": 0.25, "away": 0.45},
            }
        ),
        "missing_inputs": json.dumps([]),
        "source_score_matrices": json.dumps({"dc": [[0.1]]}),
        "fused_score_matrix": json.dumps([[0.1, 0.2], [0.3, 0.4]]),
        "weight_config_label": "test",
        "weight_config": json.dumps({"dc": 0.9}),
        "effective_weights": json.dumps({}),
        "fusion_graph": json.dumps({}),
        "model_disagreement": json.dumps({}),
        "market_blended": 1,
        "market_weight_used": 0.2,
        "market_divergence": 0.1,
        "confidence_penalty": 0.0,
        "pipeline_status": "full",
        "degraded_reasons": json.dumps([]),
        "data_fingerprint": "fingerprint",
        "source_timestamps": json.dumps({}),
        "odds_snapshot_id": None,
        "weather_snapshot_id": None,
        "injury_snapshot_id": None,
        "confidence": "medium",
        "weather_available": 1,
        "odds_available": 1,
        "lineup_available": 0,
        "injury_data_available": 0,
        "news_signals_available": 1,
    }
    columns = list(payload)
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO pre_match_snapshots ({', '.join(columns)}) VALUES ({placeholders})",
        [payload[column] for column in columns],
    )


@pytest.fixture()
def backfill_module():
    return _load_module()


def test_backfill_inserts_missing_records_and_is_idempotent(tmp_path, backfill_module):
    conn = _make_conn(tmp_path)
    _insert_snapshot(conn, "197")
    conn.execute("INSERT INTO wc26_schedule VALUES (?, ?, ?, ?, ?, ?)", ("197", "Brazil", "Norway", 1, 2, "FINISHED"))
    conn.execute("INSERT INTO teams VALUES (?, ?)", ("team-1", "Norway"))
    conn.execute(
        "INSERT INTO news_signals VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sig-1", "team-1", "injury", "positive", "evidence-1", 0.75, "Signal summary"),
    )
    conn.execute("INSERT INTO prediction_learning_log VALUES (?, ?, ?, ?)", ("log-1", "197", None, "pre-197"))
    conn.commit()

    actions = backfill_module.repair_match(conn, "197", persist=True)

    assert {action["action"] for action in actions} == {
        "insert_prediction_snapshot",
        "insert_prediction_run",
        "link_learning_log",
        "insert_postmatch_eval",
    }
    assert conn.execute("SELECT COUNT(*) FROM prediction_snapshots").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM prediction_runs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM postmatch_eval").fetchone()[0] == 1
    assert conn.execute("SELECT prediction_run_id FROM prediction_learning_log").fetchone()[0]

    component_probs = json.loads(conn.execute("SELECT component_probs FROM prediction_snapshots").fetchone()[0])
    assert {"dc", "pi", "market"}.issubset(component_probs)

    approved = json.loads(conn.execute("SELECT approved_signals FROM prediction_runs").fetchone()[0])
    assert approved == [
        {
            "id": "sig-1",
            "team": "Norway",
            "signal_type": "injury",
            "impact_direction": "positive",
            "evidence_id": "evidence-1",
            "confidence": 0.75,
            "summary_zh": "Signal summary",
        }
    ]
    eval_row = conn.execute("SELECT actual_result, top3_hit FROM postmatch_eval").fetchone()
    assert dict(eval_row) == {"actual_result": "A", "top3_hit": 1}

    second_actions = backfill_module.repair_match(conn, "197", persist=True)
    assert second_actions == [{"match_id": "197", "action": "noop"}]
    assert conn.execute("SELECT COUNT(*) FROM prediction_runs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM postmatch_eval").fetchone()[0] == 1


def test_backfill_normalizes_existing_prediction_run_signals(tmp_path, backfill_module):
    conn = _make_conn(tmp_path)
    _insert_snapshot(conn, "199")
    conn.execute("INSERT INTO wc26_schedule VALUES (?, ?, ?, ?, ?, ?)", ("199", "Brazil", "Norway", None, None, "SCHEDULED"))
    conn.execute("INSERT INTO teams VALUES (?, ?)", ("team-1", "Norway"))
    conn.execute(
        "INSERT INTO news_signals VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sig-1", "team-1", "return", "positive", "evidence-1", 0.9, "Return signal"),
    )
    conn.execute(
        """
        INSERT INTO prediction_runs
        (id, match_id, run_type, model_version, as_of_time, home_win_prob, draw_prob,
         away_win_prob, home_xg, away_xg, score_matrix, top3_scores, confidence_score,
         risk_tags, input_feature_snapshot, approved_signals, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "run-199",
            "199",
            "full",
            "4.9.0-alpha",
            "2026-07-06T06:00:00+00:00",
            0.4,
            0.2,
            0.4,
            1.1,
            1.4,
            json.dumps([[1.0]]),
            json.dumps([]),
            0.65,
            json.dumps([]),
            json.dumps({}),
            json.dumps(["sig-1"]),
            "2026-07-06T06:00:00+00:00",
        ),
    )
    conn.commit()

    actions = backfill_module.repair_match(conn, "199", persist=True)

    assert "insert_prediction_run" not in {action["action"] for action in actions}
    assert "update_approved_signals" in {action["action"] for action in actions}
    approved = json.loads(conn.execute("SELECT approved_signals FROM prediction_runs WHERE id = ?", ("run-199",)).fetchone()[0])
    assert approved[0]["id"] == "sig-1"
    assert approved[0]["team"] == "Norway"


def test_backfill_creates_missing_match_parent_from_schedule(tmp_path, backfill_module):
    conn = _make_conn(tmp_path)
    conn.executescript(
        """
        ALTER TABLE wc26_schedule ADD COLUMN match_number INTEGER;
        ALTER TABLE wc26_schedule ADD COLUMN stage TEXT;
        ALTER TABLE wc26_schedule ADD COLUMN match_date TEXT;
        ALTER TABLE wc26_schedule ADD COLUMN kickoff_time TEXT;
        ALTER TABLE wc26_schedule ADD COLUMN venue TEXT;
        ALTER TABLE wc26_schedule ADD COLUMN city TEXT;
        CREATE TABLE matches (
            id TEXT PRIMARY KEY,
            external_id TEXT,
            home_team_id TEXT,
            away_team_id TEXT,
            match_date TEXT,
            competition TEXT,
            competition_weight REAL,
            stage TEXT,
            venue TEXT,
            is_neutral_venue INTEGER,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            competition_type TEXT
        );
        """
    )
    _insert_snapshot(conn, "206")
    conn.execute("INSERT INTO teams VALUES (?, ?)", ("team-home", "Brazil"))
    conn.execute("INSERT INTO teams VALUES (?, ?)", ("team-away", "Norway"))
    conn.execute(
        """
        INSERT INTO wc26_schedule (
            id, home_team, away_team, home_goals, away_goals, match_status,
            match_number, stage, match_date, kickoff_time, venue, city
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "206", "Brazil", "Norway", None, None, "SCHEDULED",
            102, "Semifinal", "2026-07-16", "03:00", "Test Stadium", "Test City",
        ),
    )
    conn.commit()

    actions = backfill_module.repair_match(conn, "206", persist=True)

    assert "insert_matches_parent" in {item["action"] for item in actions}
    parent = conn.execute(
        "SELECT external_id, stage, status FROM matches WHERE id = ?",
        ("206",),
    ).fetchone()
    assert dict(parent) == {
        "external_id": "wc26_schedule:206",
        "stage": "Semifinal",
        "status": "scheduled",
    }
    params = json.loads(
        conn.execute("SELECT pipeline_params FROM prediction_snapshots").fetchone()[0]
    )
    assert params["stage"] == "Semifinal"
    assert params["is_neutral"] is True
