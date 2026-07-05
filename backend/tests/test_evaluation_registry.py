import json
import sqlite3
from pathlib import Path

import pytest

from app.services.candidate_experiments import (
    CandidateExperimentConfig,
    _ece,
    _shadow_gate_decision,
    run_candidate_experiment,
)
from app.services.evaluation_registry import build_evaluation_registry


def _create_registry_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE teams (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE matches (
            id TEXT PRIMARY KEY,
            home_team_id TEXT,
            away_team_id TEXT,
            match_date TEXT,
            competition TEXT,
            stage TEXT
        );
        CREATE TABLE match_results (
            match_id TEXT,
            home_goals INTEGER,
            away_goals INTEGER
        );
        CREATE TABLE wc26_schedule (
            id TEXT PRIMARY KEY,
            match_number INTEGER,
            home_team TEXT,
            away_team TEXT,
            match_date TEXT,
            kickoff_time TEXT,
            stage TEXT,
            match_status TEXT,
            home_goals INTEGER,
            away_goals INTEGER
        );
        CREATE TABLE pre_match_snapshots (
            id TEXT PRIMARY KEY,
            match_id TEXT,
            home_team TEXT,
            away_team TEXT,
            snapshot_at TEXT,
            kickoff_at TEXT,
            model_version TEXT,
            weight_config_label TEXT,
            final_home_prob REAL,
            final_draw_prob REAL,
            final_away_prob REAL,
            component_probs TEXT,
            fused_score_matrix TEXT
        );
        CREATE TABLE prediction_snapshots (
            id TEXT PRIMARY KEY,
            match_id TEXT,
            home_team TEXT,
            away_team TEXT,
            generated_at TEXT,
            model_version TEXT,
            adjusted_probs TEXT,
            baseline_probs TEXT,
            component_probs TEXT
        );
        CREATE TABLE postmatch_process_eval (
            id TEXT PRIMARY KEY,
            match_id TEXT
        );
        """
    )
    return conn


def _insert_match(conn, idx, home, away, hg, ag, *, with_schedule=True, with_process=False):
    match_id = f"m{idx}"
    home_id = f"h{idx}"
    away_id = f"a{idx}"
    match_date = f"2026-06-{10 + idx:02d}T20:00:00+00:00"
    conn.execute("INSERT INTO teams(id, name) VALUES (?, ?)", (home_id, home))
    conn.execute("INSERT INTO teams(id, name) VALUES (?, ?)", (away_id, away))
    conn.execute(
        "INSERT INTO matches(id, home_team_id, away_team_id, match_date, competition, stage) "
        "VALUES (?, ?, ?, ?, 'FIFA World Cup 2026', 'Group A - Matchday 1')",
        (match_id, home_id, away_id, match_date),
    )
    conn.execute("INSERT INTO match_results(match_id, home_goals, away_goals) VALUES (?, ?, ?)", (match_id, hg, ag))
    if with_schedule:
        conn.execute(
        "INSERT INTO wc26_schedule(id, match_number, home_team, away_team, match_date, kickoff_time, stage, match_status, home_goals, away_goals) "
        "VALUES (?, ?, ?, ?, ?, NULL, 'Group A - Matchday 1', 'FINISHED', ?, ?)",
            (f"s{idx}", idx, home, away, match_date, hg, ag),
        )
    conn.execute(
        "INSERT INTO pre_match_snapshots(id, match_id, home_team, away_team, snapshot_at, kickoff_at, "
        "model_version, weight_config_label, final_home_prob, final_draw_prob, final_away_prob, "
        "component_probs, fused_score_matrix) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"p{idx}",
            match_id,
            home,
            away,
            f"2026-06-{10 + idx:02d}T10:00:00+00:00",
            match_date,
            "4.7.0-alpha",
            "WORLD_CUP_V4.7.0_ALPHA",
            0.70 if hg > ag else 0.15,
            0.15 if hg != ag else 0.70,
            0.15 if hg >= ag else 0.70,
            json.dumps({"dc": {"home": 0.7}, "elo": {"home": 0.6}}),
            json.dumps([[0.2, 0.1], [0.3, 0.4]]),
        ),
    )
    if with_process:
        conn.execute("INSERT INTO postmatch_process_eval(id, match_id) VALUES (?, ?)", (f"pe{idx}", match_id))


def test_registry_marks_schedule_only_finished_rows(tmp_path):
    db_path = tmp_path / "registry.db"
    conn = _create_registry_db(db_path)
    _insert_match(conn, 1, "Alpha", "Beta", 2, 0, with_process=True)
    conn.execute(
        "INSERT INTO wc26_schedule(id, match_number, home_team, away_team, match_date, kickoff_time, stage, match_status, home_goals, away_goals) "
        "VALUES ('s_only', 99, 'Gamma', 'Delta', '2026-07-01T20:00:00+00:00', NULL, 'Round of 32', 'FINISHED', 1, 0)"
    )
    conn.commit()
    conn.close()

    registry = build_evaluation_registry(db_path)

    assert registry["schema_version"] == "evaluation_registry.v2"
    assert registry["summary"]["match_results_count"] == 1
    assert registry["summary"]["schedule_finished_count"] == 2
    assert registry["summary"]["schedule_only_finished_count"] == 1
    assert registry["summary"]["eligible_backtest_count"] == 1
    assert registry["summary"]["strict_count"] == 1
    schedule_only = [row for row in registry["samples"] if row["home_team"] == "Gamma"][0]
    strict = [row for row in registry["samples"] if row["home_team"] == "Alpha"][0]
    assert schedule_only["canonical_result_source"] == "wc26_schedule"
    assert "missing_canonical_result" not in schedule_only["exclusion_reasons"]
    assert "missing_pre_match_snapshot" in schedule_only["exclusion_reasons"]
    assert strict["sample_status"] == "strict"
    assert strict["leakage_status"] == "clean"
    assert strict["horizon_bucket"] == "T-24h"
    assert strict["data_availability"]["current_probabilities"] is True


def test_registry_accepts_schedule_only_with_kickoff_time_and_pre_snapshot(tmp_path):
    db_path = tmp_path / "registry.db"
    conn = _create_registry_db(db_path)
    conn.execute(
        "INSERT INTO wc26_schedule(id, match_number, home_team, away_team, match_date, kickoff_time, stage, match_status, home_goals, away_goals) "
        "VALUES ('s_only', 99, 'Gamma', 'Delta', '2026-07-01', '20:00', 'Round of 32', 'FINISHED', 1, 0)"
    )
    conn.execute(
        "INSERT INTO pre_match_snapshots(id, match_id, home_team, away_team, snapshot_at, kickoff_at, "
        "model_version, weight_config_label, final_home_prob, final_draw_prob, final_away_prob, "
        "component_probs, fused_score_matrix) VALUES ('p_schedule', 's_only', 'Gamma', 'Delta', "
        "'2026-07-01T10:00:00+00:00', NULL, '4.8.0-alpha', 'WORLD_CUP_V4.7.0_ALPHA', "
        "0.7, 0.15, 0.15, ?, ?)",
        (json.dumps({"dc": {"home": 0.7}}), json.dumps([[0.2, 0.1], [0.3, 0.4]])),
    )
    conn.commit()
    conn.close()

    row = build_evaluation_registry(db_path)["samples"][0]

    assert row["sample_status"] == "strict"
    assert row["eligible_for_backtest"] is True
    assert row["canonical_result_source"] == "wc26_schedule"
    assert row["kickoff_at"] == "2026-07-01T20:00:00"
    assert row["kickoff_source"] == "wc26_schedule.match_date+kickoff_time"
    assert row["horizon_bucket"] == "T-24h"


def test_registry_uses_pre_kickoff_prediction_snapshot_as_probability_fallback(tmp_path):
    db_path = tmp_path / "registry.db"
    conn = _create_registry_db(db_path)
    conn.execute("INSERT INTO teams(id, name) VALUES ('h1', 'Alpha')")
    conn.execute("INSERT INTO teams(id, name) VALUES ('a1', 'Beta')")
    conn.execute(
        "INSERT INTO matches(id, home_team_id, away_team_id, match_date, competition, stage) "
        "VALUES ('m1', 'h1', 'a1', '2026-06-15T20:00:00+00:00', 'FIFA World Cup 2026', 'Group A')"
    )
    conn.execute("INSERT INTO match_results(match_id, home_goals, away_goals) VALUES ('m1', 1, 0)")
    conn.execute(
        "INSERT INTO prediction_snapshots(id, match_id, home_team, away_team, generated_at, model_version, "
        "adjusted_probs, baseline_probs, component_probs) VALUES "
        "('ps1', 'm1', 'Alpha', 'Beta', '2026-06-15T10:00:00+00:00', '4.8.0-alpha', ?, NULL, ?)",
        (json.dumps({"home_win_prob": 0.6, "draw_prob": 0.25, "away_win_prob": 0.15}), json.dumps({"dc": {}})),
    )
    conn.commit()
    conn.close()

    row = build_evaluation_registry(db_path)["samples"][0]

    assert row["sample_status"] == "strict"
    assert row["pre_match_snapshot_id"] is None
    assert row["prediction_snapshot_id"] == "ps1"
    assert row["current_prob_source"] == "prediction_snapshots.adjusted_or_baseline_probs"
    assert row["current_probs"] == {"home": 0.6, "draw": 0.25, "away": 0.15}


def test_registry_rejects_post_kickoff_prediction_snapshot_fallback(tmp_path):
    db_path = tmp_path / "registry.db"
    conn = _create_registry_db(db_path)
    conn.execute("INSERT INTO teams(id, name) VALUES ('h1', 'Alpha')")
    conn.execute("INSERT INTO teams(id, name) VALUES ('a1', 'Beta')")
    conn.execute(
        "INSERT INTO matches(id, home_team_id, away_team_id, match_date, competition, stage) "
        "VALUES ('m1', 'h1', 'a1', '2026-06-15T20:00:00+00:00', 'FIFA World Cup 2026', 'Group A')"
    )
    conn.execute("INSERT INTO match_results(match_id, home_goals, away_goals) VALUES ('m1', 1, 0)")
    conn.execute(
        "INSERT INTO prediction_snapshots(id, match_id, home_team, away_team, generated_at, model_version, "
        "adjusted_probs, baseline_probs, component_probs) VALUES "
        "('ps1', 'm1', 'Alpha', 'Beta', '2026-06-15T22:00:00+00:00', '4.8.0-alpha', ?, NULL, ?)",
        (json.dumps({"home": 0.6, "draw": 0.25, "away": 0.15}), json.dumps({"dc": {}})),
    )
    conn.commit()
    conn.close()

    row = build_evaluation_registry(db_path)["samples"][0]

    assert row["sample_status"] == "rejected"
    assert row["eligible_for_backtest"] is False
    assert row["prediction_snapshot_id"] == "ps1"
    assert row["current_probs"] == {"home": 0.6, "draw": 0.25, "away": 0.15}
    assert "snapshot_after_kickoff" in row["exclusion_reasons"]


def test_registry_team_fallback_uses_latest_snapshot_before_match(tmp_path):
    db_path = tmp_path / "registry.db"
    conn = _create_registry_db(db_path)
    conn.execute("INSERT INTO teams(id, name) VALUES ('h1', 'Alpha')")
    conn.execute("INSERT INTO teams(id, name) VALUES ('a1', 'Beta')")
    conn.execute(
        "INSERT INTO matches(id, home_team_id, away_team_id, match_date, competition, stage) "
        "VALUES ('m1', 'h1', 'a1', '2026-06-15T20:00:00+00:00', 'FIFA World Cup 2026', 'Group A')"
    )
    conn.execute("INSERT INTO match_results(match_id, home_goals, away_goals) VALUES ('m1', 1, 0)")
    conn.execute(
        "INSERT INTO wc26_schedule(id, match_number, home_team, away_team, match_date, kickoff_time, stage, match_status, home_goals, away_goals) "
        "VALUES ('s1', 1, 'Alpha', 'Beta', '2026-06-15T20:00:00+00:00', NULL, 'Group A', 'FINISHED', 1, 0)"
    )
    for snapshot_id, match_id, snapshot_at, home_prob in (
        ("future", "other-match", "2026-06-16T10:00:00+00:00", 0.10),
        ("past", "other-match", "2026-06-15T10:00:00+00:00", 0.70),
    ):
        conn.execute(
            "INSERT INTO pre_match_snapshots(id, match_id, home_team, away_team, snapshot_at, kickoff_at, "
            "model_version, weight_config_label, final_home_prob, final_draw_prob, final_away_prob, "
            "component_probs, fused_score_matrix) VALUES (?, ?, 'Alpha', 'Beta', ?, "
            "'2026-06-15T20:00:00+00:00', '4.7.0-alpha', 'WORLD_CUP_V4.7.0_ALPHA', ?, 0.15, 0.15, ?, ?)",
            (
                snapshot_id,
                match_id,
                snapshot_at,
                home_prob,
                json.dumps({"dc": {"home": home_prob}}),
                json.dumps([[0.2, 0.1], [0.3, 0.4]]),
            ),
        )
    conn.commit()
    conn.close()

    registry = build_evaluation_registry(db_path)
    row = registry["samples"][0]

    assert row["pre_match_snapshot_id"] == "past"
    assert row["eligible_for_backtest"] is True


def test_registry_date_only_kickoff_is_diagnostic_not_strict(tmp_path):
    db_path = tmp_path / "registry.db"
    conn = _create_registry_db(db_path)
    conn.execute("INSERT INTO teams(id, name) VALUES ('h1', 'Alpha')")
    conn.execute("INSERT INTO teams(id, name) VALUES ('a1', 'Beta')")
    conn.execute(
        "INSERT INTO matches(id, home_team_id, away_team_id, match_date, competition, stage) "
        "VALUES ('m1', 'h1', 'a1', '2026-06-15', 'FIFA World Cup 2026', 'Group A')"
    )
    conn.execute("INSERT INTO match_results(match_id, home_goals, away_goals) VALUES ('m1', 1, 0)")
    conn.execute(
        "INSERT INTO pre_match_snapshots(id, match_id, home_team, away_team, snapshot_at, kickoff_at, "
        "model_version, weight_config_label, final_home_prob, final_draw_prob, final_away_prob, "
        "component_probs, fused_score_matrix) VALUES ('p1', 'm1', 'Alpha', 'Beta', "
        "'2026-06-15T10:00:00+00:00', NULL, '4.7.0-alpha', 'WORLD_CUP_V4.7.0_ALPHA', "
        "0.7, 0.15, 0.15, ?, ?)",
        (json.dumps({"dc": {"home": 0.7}}), json.dumps([[0.2, 0.1], [0.3, 0.4]])),
    )
    conn.commit()
    conn.close()

    row = build_evaluation_registry(db_path)["samples"][0]

    assert row["sample_status"] == "diagnostic"
    assert row["eligible_for_backtest"] is False
    assert row["kickoff_at"] is None
    assert row["horizon_bucket"] == "unknown"
    assert "snapshot_or_kickoff_time_unknown" in row["exclusion_reasons"]


def test_candidate_experiment_rejects_insufficient_samples(tmp_path):
    db_path = tmp_path / "registry.db"
    conn = _create_registry_db(db_path)
    _insert_match(conn, 1, "Alpha", "Beta", 2, 0)
    conn.commit()
    conn.close()

    result = run_candidate_experiment(
        str(db_path),
        config=CandidateExperimentConfig(candidate_name="uniform_baseline", min_sample_count=3),
    )

    assert result["status"] == "rejected"
    assert result["gate_decision"]["passed"] is False
    assert result["n_samples"] == 1


def test_candidate_experiment_outputs_paired_metrics(tmp_path):
    db_path = tmp_path / "registry.db"
    conn = _create_registry_db(db_path)
    _insert_match(conn, 1, "Alpha", "Beta", 2, 0)
    _insert_match(conn, 2, "Gamma", "Delta", 1, 1)
    _insert_match(conn, 3, "Epsilon", "Zeta", 0, 2)
    conn.commit()
    conn.close()

    result = run_candidate_experiment(
        str(db_path),
        config=CandidateExperimentConfig(candidate_name="uniform_baseline", min_sample_count=3),
    )

    assert result["status"] == "completed"
    assert result["n_samples"] == 3
    assert result["candidate_family"] == "baseline"
    assert result["sample_quality_summary"]["eligible_samples"] == 3
    assert result["metrics_current"]["brier"] < result["metrics_candidate"]["brier"]
    assert result["paired_deltas"]["brier"]["mean_delta"] > 0
    assert result["paired_deltas"]["brier"]["ci_method"] == "paired_bootstrap_percentile_v1"
    assert "sample_registry_summary" in result
    assert result["group_metrics"]["group_stage"]["metrics"]["brier"]["candidate_minus_current"] > 0
    assert result["gate_decision"]["status"] == "shadow_rejected"


def test_ece_uses_top_label_confidence():
    assert _ece([{"home": 0.8, "draw": 0.1, "away": 0.1}], [2]) == pytest.approx(0.8)


def test_shadow_gate_requires_ci_support_not_only_mean_improvement():
    decision = _shadow_gate_decision(
        {
            "brier": {"mean_delta": -0.01, "ci95": [-0.05, 0.03]},
            "logloss": {"mean_delta": -0.01, "ci95": [-0.05, -0.001]},
            "rps": {"mean_delta": -0.01, "ci95": [-0.05, -0.001]},
        }
    )

    assert decision["passed"] is False
    assert decision["status"] == "shadow_needs_more_evidence"
    assert "brier_ci_crosses_zero" in decision["reasons"]


def test_shadow_gate_rejects_noop_candidate():
    decision = _shadow_gate_decision(
        {
            "brier": {"mean_delta": 0.0, "ci95": [0.0, 0.0]},
            "logloss": {"mean_delta": 0.0, "ci95": [0.0, 0.0]},
            "rps": {"mean_delta": 0.0, "ci95": [0.0, 0.0]},
        }
    )

    assert decision["passed"] is False
    assert decision["status"] == "shadow_rejected"
    assert "fewer_than_two_supported_core_metric_improvements" in decision["reasons"]


def test_shadow_gate_rejects_key_group_degradation():
    decision = _shadow_gate_decision(
        {
            "brier": {"mean_delta": -0.01, "ci95": [-0.03, -0.001]},
            "logloss": {"mean_delta": -0.01, "ci95": [-0.03, -0.001]},
            "rps": {"mean_delta": -0.0005, "ci95": [-0.01, 0.001]},
        },
        {
            "knockout": {
                "n": 6,
                "metrics": {
                    "brier": {"candidate_minus_current": 0.03},
                    "logloss": {"candidate_minus_current": -0.01},
                    "rps": {"candidate_minus_current": -0.01},
                },
            }
        },
    )

    assert decision["passed"] is False
    assert decision["status"] == "shadow_rejected"
    assert "knockout_brier_group_degraded" in decision["reasons"]


def test_local_learning_log_schema_matches_v47_score_fields():
    db_path = Path(__file__).resolve().parents[1] / "data" / "local_stage2.db"
    if not db_path.exists():
        pytest.skip("local DB unavailable")
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as exc:
        pytest.skip(f"local DB unavailable: {exc}")
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(prediction_learning_log)")}
    finally:
        conn.close()
    assert {
        "score_log_loss",
        "score_exact_hit",
        "score_top3_hit",
        "dc_score_log_loss",
        "negbin_score_log_loss",
        "weibull_score_log_loss",
    }.issubset(cols)
