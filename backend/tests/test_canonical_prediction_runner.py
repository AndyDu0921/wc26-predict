from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from starlette.requests import Request


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def test_canonical_prediction_runner_rejects_mismatched_db_paths(tmp_path):
    from app.services.sqlite_paths import assert_canonical_sqlite_alignment

    async_db = tmp_path / "async.db"
    sync_db = tmp_path / "sync.db"
    async_db.write_text("", encoding="utf-8")
    sync_db.write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="DB path mismatch"):
        assert_canonical_sqlite_alignment(
            postgres_url=f"sqlite+aiosqlite:///{async_db.as_posix()}",
            sync_db_path=sync_db,
        )


def test_canonical_prediction_runner_accepts_matching_sqlite_path(tmp_path):
    from app.services.sqlite_paths import assert_canonical_sqlite_alignment

    db_path = tmp_path / "same.db"
    db_path.write_text("", encoding="utf-8")
    resolved = assert_canonical_sqlite_alignment(
        postgres_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
        sync_db_path=db_path,
    )
    assert resolved == db_path.resolve()


def test_canonical_prediction_runner_rejects_postgres_url():
    from app.services.sqlite_paths import assert_canonical_sqlite_alignment

    with pytest.raises(RuntimeError, match="requires POSTGRES_URL to be a SQLite URL"):
        assert_canonical_sqlite_alignment(
            postgres_url="postgresql+asyncpg://worldcup:pw@localhost/worldcup",
        )


def test_canonical_prediction_runner_persists_closed_loop_on_temp_db():
    repo_root = Path(__file__).resolve().parents[2]
    source_db = repo_root / "backend" / "data" / "local_stage2.db"
    if not source_db.exists():
        pytest.skip("local_stage2.db is required for canonical trigger smoke")

    proc = subprocess.run(
        [sys.executable, str(repo_root / "backend" / "scripts" / "smoke_canonical_trigger.py")],
        cwd=repo_root,
        text=True,
        capture_output=True,
        timeout=240,
    )
    payload = json.loads(proc.stdout)

    assert proc.returncode == 0, payload
    assert payload["passed"] is True
    assert payload["source_db_unchanged"] is True
    assert payload["run_type"] == "manual"
    assert payload["counts"]["prediction_runs"] > 0
    assert payload["counts"]["pre_match_snapshots"] > 0
    assert payload["counts"]["prediction_snapshots"] > 0
    assert payload["counts"]["feature_snapshots"] > 0
    assert payload["counts"]["evidence_items"] > 0


def test_api_prediction_trigger_calls_canonical_runner(monkeypatch):
    from app.routers import predictions
    from app.schemas.admin import TriggerPredictionRequest

    called = {}
    expected_run_id = uuid4()
    match_id = uuid4()

    async def fake_runner(*, match_id, run_type, db, mode="full"):
        called["match_id"] = match_id
        called["run_type"] = run_type
        called["db"] = db
        called["mode"] = mode
        return expected_run_id

    monkeypatch.setattr(predictions, "run_canonical_prediction", fake_runner)

    payload = TriggerPredictionRequest(run_type="manual")
    result = asyncio.run(
        predictions.trigger_prediction(
            request=_request(),
            match_id=match_id,
            payload=payload,
            _="token",
            db=object(),
        )
    )

    assert result["prediction_run_id"] == str(expected_run_id)
    assert called["match_id"] == match_id
    assert called["run_type"] == "manual"


def test_admin_prediction_trigger_calls_canonical_runner(monkeypatch):
    from app.routers import admin
    from app.schemas.admin import TriggerPredictionRequest

    expected_run_id = uuid4()
    match_id = uuid4()
    called = {}

    async def fake_runner(*, match_id, run_type, db, mode="full"):
        called["match_id"] = match_id
        called["run_type"] = run_type
        return expected_run_id

    monkeypatch.setattr(admin, "run_canonical_prediction", fake_runner)
    result = asyncio.run(
        admin.admin_trigger_prediction(
            request=_request(),
            match_id=match_id,
            payload=TriggerPredictionRequest(run_type="manual"),
            db=object(),
        )
    )
    assert str(result.prediction_run_id) == str(expected_run_id)
    assert result.status == "ok"
    assert called == {"match_id": match_id, "run_type": "manual"}


def test_worker_prediction_trigger_calls_canonical_runner(monkeypatch):
    from app.models.enums import MatchStatus
    from app.workers import tasks

    match_id = uuid4()
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    fake_match = SimpleNamespace(
        id=match_id,
        match_date=future,
        status=MatchStatus.SCHEDULED,
    )
    calls = []

    class FakeScalarResult:
        def all(self):
            return [fake_match]

    class FakeExecuteResult:
        def __init__(self, kind: str):
            self.kind = kind

        def scalars(self):
            return FakeScalarResult() if self.kind == "matches" else self

        def scalar_one_or_none(self):
            return None

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, stmt):
            text = str(stmt)
            return FakeExecuteResult("matches" if "FROM matches" in text else "existing")

        async def flush(self):
            return None

    async def fake_runner(*, match_id, run_type, db, mode="full"):
        calls.append((match_id, run_type, db, mode))
        return uuid4()

    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: FakeSession())
    monkeypatch.setattr(tasks, "run_canonical_prediction", fake_runner)

    result = asyncio.run(tasks._trigger_predictions())

    assert result == {"checked_matches": 1, "created": 2}
    assert [call[1] for call in calls] == ["t_minus_24h", "t_minus_3h"]
    assert all(call[0] == match_id for call in calls)
