import sqlite3

from app.services.model_change_proposals import (
    build_learning_log_weight_proposal,
    build_proposal_from_experiment,
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
