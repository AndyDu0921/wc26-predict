from __future__ import annotations

import json
import pickle

import pandas as pd
import pytest

from app.services.artifact_bundle import (
    load_active_bundle,
    load_verified_pickle,
    sha256_file,
    verified_artifact_path,
)


def test_verified_artifact_path_rejects_tampering(tmp_path):
    repo_root = tmp_path / "repo"
    backend = repo_root / "backend"
    artifact = backend / "artifacts" / "model.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"trusted")
    manifest = backend / "artifacts" / "active_bundle.json"
    manifest.write_text(
        json.dumps({
            "schema_version": "model_artifact_bundle.v1",
            "status": "legacy_active_unvalidated",
            "components": {
                "model": {
                    "path": "backend/artifacts/model.bin",
                    "sha256": sha256_file(artifact),
                    "size_bytes": artifact.stat().st_size,
                }
            },
        }),
        encoding="utf-8",
    )

    # The service's repository root is fixed, so use the real manifest format
    # test below for path resolution and isolate hash behavior here by swapping
    # its module constants.
    from app.services import artifact_bundle

    old_backend = artifact_bundle.BACKEND_DIR
    try:
        artifact_bundle.BACKEND_DIR = backend
        assert verified_artifact_path("model", bundle_path=manifest) == artifact.resolve()
        artifact.write_bytes(b"tampered")
        with pytest.raises(RuntimeError, match="integrity mismatch"):
            verified_artifact_path("model", bundle_path=manifest)
    finally:
        artifact_bundle.BACKEND_DIR = old_backend


def test_verified_pickle_rejects_tampering_before_deserialization(tmp_path):
    repo_root = tmp_path / "repo"
    backend = repo_root / "backend"
    artifact = backend / "artifacts" / "model.pkl"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(pickle.dumps({"trusted": True}))
    manifest = backend / "artifacts" / "active_bundle.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "model_artifact_bundle.v1",
                "status": "legacy_active_unvalidated",
                "components": {
                    "model": {
                        "path": "backend/artifacts/model.pkl",
                        "sha256": sha256_file(artifact),
                        "size_bytes": artifact.stat().st_size,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    from app.services import artifact_bundle

    old_backend = artifact_bundle.BACKEND_DIR
    try:
        artifact_bundle.BACKEND_DIR = backend
        assert load_verified_pickle("model", bundle_path=manifest) == {"trusted": True}
        artifact.write_bytes(pickle.dumps({"trusted": False}))
        with pytest.raises(RuntimeError, match="integrity mismatch"):
            load_verified_pickle("model", bundle_path=manifest)
    finally:
        artifact_bundle.BACKEND_DIR = old_backend


def test_current_active_bundle_verifies_registered_core_components():
    for component in (
        "dixon_coles",
        "tabular_enhancer",
        "elo",
        "pi_rating",
        "weibull",
        "calibrator",
        "calibrator_wc",
        "stacking_meta_learner",
        "conformal_predictor",
    ):
        assert verified_artifact_path(component).is_file()


def test_active_bundle_rejects_unvalidated_candidate_status(tmp_path):
    manifest = tmp_path / "active_bundle.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "model_artifact_bundle.v1",
                "status": "candidate_unvalidated",
                "components": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not eligible for production"):
        load_active_bundle(manifest)


def test_promoted_bundle_requires_structured_evidence(tmp_path):
    manifest = tmp_path / "active_bundle.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "model_artifact_bundle.v1",
                "status": "promoted",
                "promotion_evidence": False,
                "components": {},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="promotion evidence"):
        load_active_bundle(manifest)


def test_promotion_requires_same_cohort_paired_gate_evidence(tmp_path, monkeypatch):
    from scripts import register_artifact_bundle

    monkeypatch.setattr(register_artifact_bundle, "REPO_ROOT", tmp_path)
    evidence = tmp_path / "experiment.json"
    evidence.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "candidate_name": "dynamic_dixon_coles",
                        "required_model_cohort": "4.12.0-alpha",
                        "sample_registry_hash": "registry-hash",
                        "n_samples": 50,
                        "gate_decision": {"passed": True},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = register_artifact_bundle._promotion_evidence(str(evidence))

    assert result["candidate_names"] == ["dynamic_dixon_coles"]
    assert result["model_cohorts"] == ["4.12.0-alpha"]
    assert len(result["sha256"]) == 64


def test_promotion_rejects_pooled_or_underpowered_evidence(tmp_path, monkeypatch):
    from scripts import register_artifact_bundle

    monkeypatch.setattr(register_artifact_bundle, "REPO_ROOT", tmp_path)
    evidence = tmp_path / "experiment.json"
    evidence.write_text(
        json.dumps(
            {
                "candidate_name": "dynamic_dixon_coles",
                "required_model_cohort": None,
                "sample_registry_hash": "registry-hash",
                "n_samples": 29,
                "gate_decision": {"passed": True},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="same-cohort"):
        register_artifact_bundle._promotion_evidence(str(evidence))


def test_training_fingerprint_changes_when_row_content_changes():
    from scripts.train_models import compute_fingerprint

    base = pd.DataFrame(
        [
            {
                "home_team": "France",
                "away_team": "Spain",
                "home_goals": 1,
                "away_goals": 0,
                "match_date": pd.Timestamp("2026-07-10T00:00:00Z"),
                "competition_weight": 1.0,
                "is_neutral_venue": True,
                "competition": "FIFA World Cup 2026",
                "competition_type": "national",
                "stage": "Quarterfinal",
                "home_xg": 1.2,
                "away_xg": 0.8,
            }
        ]
    )
    changed = base.copy()
    changed.loc[0, "away_goals"] = 1

    assert compute_fingerprint(base) != compute_fingerprint(changed)
