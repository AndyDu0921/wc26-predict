from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from scripts.run_postmatch_complete import (
    _learning_log_prediction_run_id,
    _upsert_postmatch_eval,
)


class _Result:
    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeDb:
    def __init__(self):
        self.existing = None
        self.calls = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params or {}))
        if "SELECT id" in sql and "FROM postmatch_eval" in sql:
            return _Result(self.existing)
        if "INSERT INTO postmatch_eval" in sql:
            self.existing = {"id": (params or {})["id"]}
        return _Result()


def test_learning_log_prediction_run_id_helper():
    assert _learning_log_prediction_run_id(None) is None
    assert _learning_log_prediction_run_id(SimpleNamespace(prediction_run_id="")) is None
    assert _learning_log_prediction_run_id(SimpleNamespace(prediction_run_id="run-1")) == "run-1"


def test_postmatch_eval_upsert_is_idempotent_by_prediction_run_id():
    db = _FakeDb()

    first = asyncio.run(
        _upsert_postmatch_eval(
            db,
            prediction_run_id="run-1",
            home_score=0,
            away_score=1,
            probs=[0.52, 0.23, 0.25],
            top_scores=[{"score": "0:1", "prob": 0.12}],
            brier=0.61,
        )
    )
    second = asyncio.run(
        _upsert_postmatch_eval(
            db,
            prediction_run_id="run-1",
            home_score=0,
            away_score=1,
            probs=[0.52, 0.23, 0.25],
            top_scores=[{"score": "0:1", "prob": 0.12}],
            brier=0.61,
        )
    )

    assert first["action"] == "inserted"
    assert first["top3_hit"] is True
    assert second["action"] == "updated"
    assert sum("INSERT INTO postmatch_eval" in sql for sql, _ in db.calls) == 1
    assert sum("UPDATE postmatch_eval" in sql for sql, _ in db.calls) == 1
