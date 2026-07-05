import json
import sqlite3

from app.services.model_change_proposals import (
    build_data_repair_proposal_from_repair_report,
    build_learning_log_weight_proposal,
    build_proposal_from_experiment,
    build_registry_feature_rule_proposal,
    persist_model_change_proposal,
)


def _proposal_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE prediction_learning_log (
            status TEXT,
            learning_weight REAL,
            learning_tier TEXT,
            dc_marginal REAL,
            enhancer_marginal REAL,
            elo_marginal REAL,
            market_marginal REAL,
            signal_marginal REAL,
            score_log_loss REAL,
            score_exact_hit INTEGER,
            score_top3_hit INTEGER
        );
        CREATE TABLE model_change_proposals (
            id TEXT PRIMARY KEY,
            proposal_type TEXT,
            candidate_name TEXT,
            source TEXT,
            status TEXT,
            sample_registry_hash TEXT,
            target_table TEXT,
            target_key TEXT,
            base_payload TEXT,
            candidate_payload TEXT,
            metrics TEXT,
            gate_decision TEXT,
            evidence TEXT,
            notes TEXT
        );
        """
    )
    return conn


def test_learning_log_weight_proposal_is_rejected_when_evidence_is_insufficient(tmp_path):
    db_path = tmp_path / "proposal.db"
    conn = _proposal_db(db_path)
    conn.execute(
        "INSERT INTO prediction_learning_log VALUES ('active', 1.0, 'full', 0.01, -0.02, 0.0, NULL, NULL, 2.1, 0, 1)"
    )
    conn.commit()
    conn.close()

    proposal = build_learning_log_weight_proposal(db_path, sample_registry_hash="abc", min_active_logs=30)

    assert proposal.status == "proposal_rejected"
    assert proposal.gate_decision["passed"] is False
    assert proposal.candidate_payload["production_mutation"] is False


def test_model_change_proposal_persistence_is_idempotent(tmp_path):
    db_path = tmp_path / "proposal.db"
    conn = _proposal_db(db_path)
    conn.close()
    proposal = build_proposal_from_experiment(
        {
            "candidate_name": "dynamic_dixon_coles",
            "champion_name": "current_fusion",
            "sample_registry_hash": "abc",
            "experiment_id": "exp-1",
            "status": "completed",
            "n_samples": 40,
            "gate_decision": {"passed": False, "status": "shadow_needs_more_evidence"},
        }
    )

    first = persist_model_change_proposal(db_path, proposal)
    second = persist_model_change_proposal(db_path, proposal)

    assert first["inserted"] is True
    assert second["inserted"] is False
    assert first["fingerprint"] == second["fingerprint"]


def test_model_change_proposal_matches_existing_row_with_stale_fingerprint(tmp_path):
    db_path = tmp_path / "proposal.db"
    conn = _proposal_db(db_path)
    proposal = build_proposal_from_experiment(
        {
            "candidate_name": "dynamic_dixon_coles",
            "champion_name": "current_fusion",
            "sample_registry_hash": "abc",
            "experiment_id": "exp-1",
            "status": "completed",
            "n_samples": 40,
            "gate_decision": {"passed": False, "status": "shadow_needs_more_evidence"},
        }
    )
    conn.execute(
        """
        INSERT INTO model_change_proposals (
            id, proposal_type, candidate_name, source, status,
            sample_registry_hash, target_table, target_key,
            base_payload, candidate_payload, metrics, gate_decision,
            evidence, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "existing",
            proposal.proposal_type,
            proposal.candidate_name,
            proposal.source,
            proposal.status,
            proposal.sample_registry_hash,
            proposal.target_table,
            proposal.target_key,
            json.dumps(proposal.base_payload),
            '{"candidate_name":"dynamic_dixon_coles","experiment_id":"old-random","shadow_only":true}',
            json.dumps(proposal.metrics),
            json.dumps(proposal.gate_decision),
            json.dumps({**proposal.evidence, "fingerprint": "stale"}),
            proposal.notes,
        ),
    )
    conn.commit()
    conn.close()

    result = persist_model_change_proposal(db_path, proposal)

    assert result["inserted"] is False
    assert result["id"] == "existing"


def test_experiment_proposal_fingerprint_ignores_volatile_experiment_id():
    base = {
        "candidate_name": "dynamic_dixon_coles",
        "champion_name": "current_fusion",
        "sample_registry_hash": "abc",
        "status": "completed",
        "n_samples": 40,
        "gate_decision": {"passed": False, "status": "shadow_needs_more_evidence"},
        "paired_deltas": {"brier": {"mean_delta": -0.01}},
    }

    first = build_proposal_from_experiment({**base, "experiment_id": "exp-1"})
    second = build_proposal_from_experiment({**base, "experiment_id": "exp-2"})

    assert first.fingerprint() == second.fingerprint()


def test_learning_log_weight_proposal_requires_walk_forward_even_with_many_logs(tmp_path):
    db_path = tmp_path / "proposal.db"
    conn = _proposal_db(db_path)
    for _ in range(31):
        conn.execute(
            "INSERT INTO prediction_learning_log VALUES ('active', 1.0, 'full', 0.01, 0.02, 0.03, NULL, NULL, 2.1, 0, 1)"
        )
    conn.commit()
    conn.close()

    proposal = build_learning_log_weight_proposal(db_path, sample_registry_hash="abc", min_active_logs=30)

    assert proposal.status == "proposal_needs_backtest"
    assert proposal.gate_decision["passed"] is False
    assert proposal.gate_decision["reasons"] == ["requires_paired_walk_forward_experiment"]


def test_registry_feature_rule_proposal_tracks_data_quality_repairs():
    registry = {
        "registry_hash": "hash-1",
        "summary": {
            "total_samples": 3,
            "strict_count": 1,
            "diagnostic_count": 1,
            "rejected_count": 1,
            "with_pre_match_snapshot": 1,
            "with_prediction_snapshot": 1,
            "with_process_eval": 0,
            "source_result_conflicts": 1,
        },
        "samples": [
            {"leakage_status": "clean", "exclusion_reasons": []},
            {
                "leakage_status": "no_pre_match_snapshot",
                "exclusion_reasons": [
                    "missing_pre_match_snapshot",
                    "snapshot_or_kickoff_time_unknown",
                    "missing_current_probabilities",
                ],
            },
            {
                "leakage_status": "result_conflict",
                "exclusion_reasons": ["result_conflict_between_sources"],
            },
        ],
    }

    proposal = build_registry_feature_rule_proposal(registry)

    assert proposal.proposal_type == "feature-rule"
    assert proposal.status == "proposal_pending_data_repair"
    assert proposal.gate_decision["passed"] is False
    assert proposal.candidate_payload["production_mutation"] is False
    actions = {item["action"] for item in proposal.candidate_payload["recommended_actions"]}
    assert "backfill_or_import_pre_match_snapshots" in actions
    assert "normalize_snapshot_and_kickoff_timestamps" in actions
    assert "reconcile_conflicting_result_sources" in actions


def test_experiment_proposal_types_separate_calibrator_and_stacking():
    calibrator = build_proposal_from_experiment(
        {
            "candidate_name": "dirichlet_calibration_candidate",
            "candidate_family": "calibrator",
            "sample_registry_hash": "abc",
            "gate_decision": {"passed": False},
        }
    )
    stacking = build_proposal_from_experiment(
        {
            "candidate_name": "proper_scoring_stacking_candidate",
            "candidate_family": "stacking",
            "sample_registry_hash": "abc",
            "gate_decision": {"passed": False},
        }
    )

    assert calibrator.proposal_type == "calibrator"
    assert stacking.proposal_type == "stacking"
    assert calibrator.candidate_payload["shadow_only"] is True
    assert stacking.candidate_payload["shadow_only"] is True


def test_data_repair_proposal_from_repair_report_is_proposal_only():
    proposal = build_data_repair_proposal_from_repair_report(
        {
            "schema_version": "evaluation_registry_repair_report.v1",
            "registry_hash": "hash-1",
            "summary": {"strict_count": 25},
            "repair_summary": {
                "reported_samples": 2,
                "sample_status_counts": {"diagnostic": 2},
                "action_counts": {"import_real_pre_match_snapshot": 2},
                "potentially_promotable_count": 2,
                "must_remain_rejected_count": 0,
            },
        }
    )

    assert proposal.proposal_type == "data-repair"
    assert proposal.status == "proposal_pending_data_repair"
    assert proposal.gate_decision["passed"] is False
    assert proposal.candidate_payload["production_mutation"] is False
    assert proposal.candidate_payload["historical_report_mutation"] is False
