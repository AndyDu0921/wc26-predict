"""Shared helpers for deprecated accuracy script wrappers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.accuracy_experiment_store import persist_experiment_result
from app.services.candidate_experiments import CandidateExperimentConfig, run_candidate_experiment


def run_accuracy_wrapper(
    *,
    db_path: str,
    competition: str,
    candidates: list[str],
    min_sample_count: int,
    output: str,
    persist: bool,
    wrapper_name: str,
    deprecated_args: dict[str, Any] | None = None,
) -> int:
    results = [
        run_candidate_experiment(
            db_path,
            config=CandidateExperimentConfig(
                candidate_name=candidate_name,
                min_sample_count=min_sample_count,
                competition=competition,
                include_predictions=persist,
            ),
        )
        for candidate_name in candidates
    ]
    persisted = [persist_experiment_result(db_path, result) for result in results] if persist else None
    payload = {
        "schema_version": "legacy_accuracy_wrapper.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "wrapper_name": wrapper_name,
        "canonical_entrypoint": "backend/scripts/run_accuracy_experiments.py",
        "db_path": db_path,
        "competition": competition,
        "min_sample_count": min_sample_count,
        "candidates": candidates,
        "deprecated_args": deprecated_args or {},
        "results": results,
        "persisted": persisted,
        "notes": (
            "Compatibility wrapper only. It runs shadow accuracy experiments "
            "and does not modify production weights, model artifacts, or reports."
        ),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0
