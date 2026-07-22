from __future__ import annotations

from app.workers.tasks import _materialize_calibrator_candidate


def _records(count: int = 30) -> list[dict[str, object]]:
    outcomes = ("H", "D", "A")
    return [
        {
            "prediction_run_id": f"run-{idx:03d}",
            "home_win_prob": 0.50 if idx % 3 == 0 else 0.25,
            "draw_prob": 0.50 if idx % 3 == 1 else 0.25,
            "away_win_prob": 0.50 if idx % 3 == 2 else 0.25,
            "actual_result": outcomes[idx % 3],
        }
        for idx in range(count)
    ]


def test_calibrator_worker_writes_idempotent_candidate_only(tmp_path):
    active = tmp_path / "active-calibrator.json"
    active.write_text("unchanged", encoding="utf-8")
    candidate_root = tmp_path / "candidates"

    first = _materialize_calibrator_candidate(
        _records(),
        candidate_root=candidate_root,
        model_cohort="4.12.0-alpha",
    )
    second = _materialize_calibrator_candidate(
        _records(),
        candidate_root=candidate_root,
        model_cohort="4.12.0-alpha",
    )

    assert first["status"] == "candidate_unvalidated"
    assert second["status"] == "exists"
    assert first["active_artifact_changed"] is False
    assert active.read_text(encoding="utf-8") == "unchanged"


def test_calibrator_worker_rejects_underpowered_same_cohort(tmp_path):
    result = _materialize_calibrator_candidate(
        _records(6),
        candidate_root=tmp_path / "candidates",
        model_cohort="4.12.0-alpha",
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "insufficient_same_cohort_samples"
    assert not (tmp_path / "candidates").exists()
