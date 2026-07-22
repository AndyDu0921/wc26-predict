"""Evaluation registry for leak-aware paired model experiments.

The registry reconciles the multiple WC26 result/snapshot tables into one
auditable sample list.  It is intentionally read-only: callers can use the
rows for backtests and diagnostics, but this module never mutates weights,
artifacts, reports, or source data.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.services.sqlite_paths import current_sync_sqlite_path

BACKEND_DIR = Path(__file__).resolve().parents[2]

DEFAULT_DB_PATH = current_sync_sqlite_path()
WC26_COMPETITION = "FIFA World Cup 2026"
SCHEDULE_TIMEZONE = ZoneInfo("Asia/Shanghai")
SCHEDULE_RECONCILIATION_WINDOW = timedelta(hours=36)


@dataclass(frozen=True)
class EvaluationRegistryRow:
    sample_id: str
    sample_status: str
    canonical_match_id: str | None
    canonical_result_source: str | None
    home_team: str
    away_team: str
    match_date: str
    kickoff_at: str | None
    kickoff_source: str | None
    as_of_time: str | None
    horizon_hours: float | None
    horizon_bucket: str
    stage: str
    is_neutral: bool | None
    match_result_id: str | None
    schedule_id: str | None
    schedule_match_number: int | None
    actual_home_goals: int | None
    actual_away_goals: int | None
    schedule_home_goals: int | None
    schedule_away_goals: int | None
    has_canonical_result: bool
    has_match_result: bool
    has_schedule_result: bool
    has_pre_match_snapshot: bool
    has_prediction_snapshot: bool
    has_process_eval: bool
    pre_match_snapshot_id: str | None
    pre_match_snapshot_at: str | None
    pre_match_kickoff_at: str | None
    prediction_snapshot_id: str | None
    prediction_snapshot_at: str | None
    model_version: str | None
    model_cohort: str
    weight_config_label: str | None
    current_prob_source: str | None
    component_count: int
    data_completeness_score: float
    data_availability: dict[str, bool]
    leakage_status: str
    source_result_conflict: bool
    snapshot_before_kickoff: bool | None
    eligible_for_backtest: bool
    exclusion_reasons: list[str]
    current_probs: dict[str, float] | None
    component_probs: dict[str, Any]
    probability_quality_status: str
    probability_quality_issues: list[str]
    score_matrix: list[list[float]] | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_evaluation_registry(
    db_path: str | Path | None = None,
    *,
    competition: str = WC26_COMPETITION,
) -> dict[str, Any]:
    """Build an auditable evaluation registry from local SQLite tables."""
    path = Path(db_path or DEFAULT_DB_PATH)
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        match_rows = _load_match_results(conn, competition)
        schedule_rows = _load_schedule_results(conn)
        samples: list[EvaluationRegistryRow] = []

        consumed_schedule_ids: set[str] = set()
        for match in match_rows:
            schedule = _reconcile_schedule_row(match, schedule_rows, consumed_schedule_ids)
            if schedule:
                consumed_schedule_ids.add(str(schedule["schedule_id"]))
            samples.append(_build_row(conn, match=match, schedule=schedule))

        for schedule in schedule_rows:
            if str(schedule["schedule_id"]) in consumed_schedule_ids:
                continue
            samples.append(_build_row(conn, match=None, schedule=schedule))

        payload_rows = [row.to_dict() for row in sorted(samples, key=lambda item: item.sample_id)]
        summary = _summarize(payload_rows)
        registry_hash = _stable_hash({"samples": payload_rows, "summary": summary})
        return {
            "schema_version": "evaluation_registry.v3",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "db_path": str(path),
            "competition": competition,
            "registry_hash": registry_hash,
            "summary": summary,
            "samples": payload_rows,
        }
    finally:
        conn.close()


def _load_match_results(conn: sqlite3.Connection, competition: str) -> list[sqlite3.Row]:
    if not _has_table(conn, "matches") or not _has_table(conn, "match_results"):
        return []
    neutral_select = (
        "COALESCE(m.is_neutral_venue, 0)"
        if _has_column(conn, "matches", "is_neutral_venue")
        else "0"
    )
    return list(
        conn.execute(
            f"""
            SELECT
                CAST(m.id AS TEXT) AS match_id,
                ht.name AS home_team,
                at.name AS away_team,
                mr.home_goals AS home_goals,
                mr.away_goals AS away_goals,
                m.match_date AS match_date,
                COALESCE(m.stage, '') AS stage,
                {neutral_select} AS is_neutral_venue
            FROM matches m
            JOIN teams ht ON m.home_team_id = ht.id
            JOIN teams at ON m.away_team_id = at.id
            JOIN match_results mr ON m.id = mr.match_id
            WHERE m.competition = ?
              AND mr.home_goals IS NOT NULL
              AND mr.away_goals IS NOT NULL
            ORDER BY m.match_date ASC
            """,
            (competition,),
        )
    )


def _load_schedule_results(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    if not _has_table(conn, "wc26_schedule"):
        return []
    kickoff_select = "kickoff_time" if _has_column(conn, "wc26_schedule", "kickoff_time") else "NULL AS kickoff_time"
    rows = conn.execute(
        f"""
        SELECT
            CAST(id AS TEXT) AS schedule_id,
            match_number,
            home_team,
            away_team,
            home_goals,
            away_goals,
            match_date,
            {kickoff_select},
            COALESCE(stage, '') AS stage,
            match_status
        FROM wc26_schedule
        WHERE match_status = 'FINISHED'
          AND home_goals IS NOT NULL
          AND away_goals IS NOT NULL
        ORDER BY match_date ASC
        """
    ).fetchall()
    return [
        row for row in rows
        if row["home_team"] and row["away_team"] and row["match_date"]
    ]


def _reconcile_schedule_row(
    match: sqlite3.Row,
    schedule_rows: list[sqlite3.Row],
    consumed_schedule_ids: set[str],
) -> sqlite3.Row | None:
    """Resolve one result row to one schedule row across UTC/local dates.

    Numeric/manual match IDs intentionally mirror ``wc26_schedule.id`` for
    current tournament data, so that is the strongest identity.  The fallback
    compares the team pair and actual kickoff instants instead of date strings.
    """
    match_id = str(match["match_id"])
    direct = [
        row for row in schedule_rows
        if str(row["schedule_id"]) == match_id
        and str(row["schedule_id"]) not in consumed_schedule_ids
    ]
    if len(direct) == 1:
        return direct[0]

    pair_candidates = [
        row for row in schedule_rows
        if str(row["schedule_id"]) not in consumed_schedule_ids
        and _norm(row["home_team"]) == _norm(match["home_team"])
        and _norm(row["away_team"]) == _norm(match["away_team"])
    ]
    if not pair_candidates:
        return None
    if len(pair_candidates) == 1:
        candidate = pair_candidates[0]
        if _kickoffs_reconcile(match["match_date"], _schedule_kickoff_at(candidate)):
            return candidate
        return None

    match_dt = _parse_dt(_as_optional_str(match["match_date"]))
    if match_dt is None:
        return None
    ranked: list[tuple[float, sqlite3.Row]] = []
    for candidate in pair_candidates:
        schedule_dt = _parse_dt(_schedule_kickoff_at(candidate))
        if schedule_dt is None:
            continue
        delta = abs((match_dt - schedule_dt).total_seconds())
        if delta <= SCHEDULE_RECONCILIATION_WINDOW.total_seconds():
            ranked.append((delta, candidate))
    ranked.sort(key=lambda item: item[0])
    if not ranked or (len(ranked) > 1 and ranked[0][0] == ranked[1][0]):
        return None
    return ranked[0][1]


def _kickoffs_reconcile(left: Any, right: Any) -> bool:
    left_dt = _parse_dt(_as_optional_str(left))
    right_dt = _parse_dt(_as_optional_str(right))
    if left_dt is None or right_dt is None:
        return False
    return abs(left_dt - right_dt) <= SCHEDULE_RECONCILIATION_WINDOW


def _build_row(
    conn: sqlite3.Connection,
    *,
    match: sqlite3.Row | None,
    schedule: sqlite3.Row | None,
) -> EvaluationRegistryRow:
    home_team = str((match or schedule)["home_team"])
    away_team = str((match or schedule)["away_team"])
    match_date = str((match or schedule)["match_date"])
    stage = str((match["stage"] if match else None) or (schedule["stage"] if schedule else "") or "")
    is_neutral = bool(match["is_neutral_venue"]) if match is not None else True
    match_id = str(match["match_id"]) if match else None
    schedule_id = str(schedule["schedule_id"]) if schedule else None
    schedule_match_number = int(schedule["match_number"]) if schedule else None
    actual_home = int(match["home_goals"]) if match else None
    actual_away = int(match["away_goals"]) if match else None
    schedule_home = int(schedule["home_goals"]) if schedule else None
    schedule_away = int(schedule["away_goals"]) if schedule else None
    canonical_home = actual_home if match else schedule_home
    canonical_away = actual_away if match else schedule_away
    canonical_result_source = "match_results" if match else ("wc26_schedule" if schedule else None)
    kickoff_at, kickoff_source = _resolve_kickoff_at(match, schedule)
    sample_id = _sample_key(home_team, away_team, kickoff_at or match_date)

    pre_snapshot = _latest_pre_match_snapshot(
        conn,
        match_id,
        schedule_id,
        schedule_match_number,
        home_team,
        away_team,
        match_date,
        kickoff_at,
    )
    prediction_snapshot = _latest_prediction_snapshot(conn, match_id, home_team, away_team, match_date, kickoff_at)
    process_eval = _has_process_eval(conn, match_id, schedule_id)

    current_probs = None
    current_prob_source = None
    score_matrix = None
    component_count = 0
    component_probs: dict[str, Any] = {}
    model_version = None
    weight_config_label = None
    snapshot_at = None
    pre_snapshot_at = None
    pre_snapshot_id = None

    if pre_snapshot is not None:
        pre_snapshot_id = str(pre_snapshot["id"])
        pre_snapshot_at = _as_optional_str(pre_snapshot["snapshot_at"])
        if kickoff_at is None:
            kickoff_at = _canonical_kickoff_at(_as_optional_str(pre_snapshot["kickoff_at"]))
            kickoff_source = "pre_match_snapshots.kickoff_at" if kickoff_at else kickoff_source

    prediction_snapshot_id = None
    prediction_snapshot_at = None
    if prediction_snapshot is not None:
        prediction_snapshot_id = str(prediction_snapshot["id"])
        prediction_snapshot_at = _as_optional_str(prediction_snapshot["generated_at"])

    pre_probs = _current_probs_from_pre_snapshot(pre_snapshot) if pre_snapshot is not None else None
    prediction_probs = (
        _current_probs_from_prediction_snapshot(prediction_snapshot)
        if prediction_snapshot is not None
        else None
    )
    use_prediction_primary = _should_use_prediction_snapshot_primary(
        pre_snapshot_at=pre_snapshot_at,
        pre_probs=pre_probs,
        prediction_snapshot_at=prediction_snapshot_at,
        prediction_probs=prediction_probs,
        kickoff_at=kickoff_at,
    )

    if pre_snapshot is not None and not use_prediction_primary:
        snapshot_at = pre_snapshot_at
        model_version = _as_optional_str(pre_snapshot["model_version"])
        weight_config_label = _as_optional_str(pre_snapshot["weight_config_label"])
        current_probs = pre_probs
        current_prob_source = "pre_match_snapshots.final_probs" if current_probs is not None else None
        score_matrix = _json_loads(pre_snapshot["fused_score_matrix"])
        parsed_components = _json_loads(pre_snapshot["component_probs"])
        component_probs = parsed_components if isinstance(parsed_components, dict) else {}
        component_count = _component_count(component_probs)

    if prediction_snapshot is not None:
        if model_version is None:
            model_version = _as_optional_str(prediction_snapshot["model_version"])
        if snapshot_at is None or use_prediction_primary:
            snapshot_at = prediction_snapshot_at
        if current_probs is None or use_prediction_primary:
            current_probs = prediction_probs
            if current_probs is not None:
                current_prob_source = "prediction_snapshots.adjusted_or_baseline_probs"
                weight_config_label = "prediction_snapshot.adjusted_probs"
        if component_count == 0:
            parsed_components = _json_loads(_row_get(prediction_snapshot, "component_probs"))
            component_probs = parsed_components if isinstance(parsed_components, dict) else {}
            component_count = _component_count(component_probs)

    probability_quality_issues = _probability_quality_issues(current_probs)
    probability_quality_status = (
        "missing" if current_probs is None
        else "boundary" if probability_quality_issues
        else "valid"
    )
    model_cohort = _model_cohort(model_version)

    source_conflict = (
        match is not None
        and schedule is not None
        and (actual_home != schedule_home or actual_away != schedule_away)
    )
    horizon_hours = _horizon_hours(snapshot_at, kickoff_at)
    horizon_bucket = _horizon_bucket(horizon_hours)
    snapshot_before_kickoff = _snapshot_before_kickoff(snapshot_at, kickoff_at)
    completeness = _completeness_score(
        has_match=match is not None,
        has_schedule=schedule is not None,
        has_pre_snapshot=pre_snapshot is not None,
        has_prediction_snapshot=prediction_snapshot is not None,
        has_process_eval=process_eval,
        has_probs=current_probs is not None,
    )
    data_availability = {
        "match_result": match is not None,
        "schedule_result": schedule is not None,
        "canonical_result": canonical_home is not None and canonical_away is not None,
        "pre_match_snapshot": pre_snapshot is not None,
        "prediction_snapshot": prediction_snapshot is not None,
        "process_eval": process_eval,
        "current_probabilities": current_probs is not None,
        "score_matrix": _valid_matrix(score_matrix),
    }
    exclusions = []
    if canonical_home is None or canonical_away is None:
        exclusions.append("missing_canonical_result")
    if pre_snapshot is None and prediction_snapshot is None:
        exclusions.append("missing_pre_match_snapshot")
    if source_conflict:
        exclusions.append("result_conflict_between_sources")
    if snapshot_before_kickoff is False:
        exclusions.append("snapshot_after_kickoff")
    if snapshot_before_kickoff is None:
        exclusions.append("snapshot_or_kickoff_time_unknown")
    if current_probs is None:
        exclusions.append("missing_current_probabilities")
    leakage_status = _leakage_status(
        snapshot_before_kickoff=snapshot_before_kickoff,
        source_conflict=source_conflict,
        has_snapshot=pre_snapshot is not None or prediction_snapshot is not None,
    )
    sample_status = _sample_status(
        eligible=not exclusions,
        source_conflict=source_conflict,
        snapshot_before_kickoff=snapshot_before_kickoff,
    )

    return EvaluationRegistryRow(
        sample_id=sample_id,
        sample_status=sample_status,
        canonical_match_id=match_id or schedule_id,
        canonical_result_source=canonical_result_source,
        home_team=home_team,
        away_team=away_team,
        match_date=match_date,
        kickoff_at=kickoff_at,
        kickoff_source=kickoff_source,
        as_of_time=snapshot_at,
        horizon_hours=horizon_hours,
        horizon_bucket=horizon_bucket,
        stage=stage,
        is_neutral=is_neutral,
        match_result_id=match_id,
        schedule_id=schedule_id,
        schedule_match_number=schedule_match_number,
        actual_home_goals=canonical_home,
        actual_away_goals=canonical_away,
        schedule_home_goals=schedule_home,
        schedule_away_goals=schedule_away,
        has_canonical_result=canonical_home is not None and canonical_away is not None,
        has_match_result=match is not None,
        has_schedule_result=schedule is not None,
        has_pre_match_snapshot=pre_snapshot is not None,
        has_prediction_snapshot=prediction_snapshot is not None,
        has_process_eval=process_eval,
        pre_match_snapshot_id=pre_snapshot_id,
        pre_match_snapshot_at=pre_snapshot_at,
        pre_match_kickoff_at=kickoff_at,
        prediction_snapshot_id=prediction_snapshot_id,
        prediction_snapshot_at=prediction_snapshot_at,
        model_version=model_version,
        model_cohort=model_cohort,
        weight_config_label=weight_config_label,
        current_prob_source=current_prob_source,
        component_count=component_count,
        data_completeness_score=completeness,
        data_availability=data_availability,
        leakage_status=leakage_status,
        source_result_conflict=source_conflict,
        snapshot_before_kickoff=snapshot_before_kickoff,
        eligible_for_backtest=not exclusions,
        exclusion_reasons=exclusions,
        current_probs=current_probs,
        component_probs=component_probs,
        probability_quality_status=probability_quality_status,
        probability_quality_issues=probability_quality_issues,
        score_matrix=score_matrix if _valid_matrix(score_matrix) else None,
    )


def _latest_pre_match_snapshot(
    conn: sqlite3.Connection,
    match_id: str | None,
    schedule_id: str | None,
    schedule_match_number: int | None,
    home_team: str,
    away_team: str,
    match_date: str,
    kickoff_at: str | None,
) -> sqlite3.Row | None:
    if not _has_table(conn, "pre_match_snapshots"):
        return None
    ids = [str(item) for item in (match_id, schedule_id, schedule_match_number) if item is not None]
    candidates: list[sqlite3.Row] = []
    seen: set[str] = set()
    if ids:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"""
            SELECT * FROM pre_match_snapshots
            WHERE CAST(match_id AS TEXT) IN ({placeholders})
            ORDER BY snapshot_at DESC
            """,
            ids,
        ).fetchall()
        _extend_matching_snapshot_rows(candidates, seen, rows, home_team, away_team)
    rows = conn.execute(
        """
        SELECT * FROM pre_match_snapshots
        WHERE home_team = ? AND away_team = ?
        ORDER BY snapshot_at DESC
        """,
        (home_team, away_team),
    ).fetchall()
    _extend_matching_snapshot_rows(candidates, seen, rows, home_team, away_team)
    return _choose_snapshot_for_kickoff(candidates, kickoff_at, match_date)


def _latest_prediction_snapshot(
    conn: sqlite3.Connection,
    match_id: str | None,
    home_team: str,
    away_team: str,
    match_date: str,
    kickoff_at: str | None,
) -> sqlite3.Row | None:
    if not _has_table(conn, "prediction_snapshots"):
        return None
    candidates: list[sqlite3.Row] = []
    seen: set[str] = set()
    if match_id:
        rows = conn.execute(
            """
            SELECT * FROM prediction_snapshots
            WHERE CAST(match_id AS TEXT) = ?
            ORDER BY generated_at DESC
            """,
            (match_id,),
        ).fetchall()
        _extend_matching_snapshot_rows(candidates, seen, rows, home_team, away_team)
    rows = conn.execute(
        """
        SELECT * FROM prediction_snapshots
        WHERE home_team = ? AND away_team = ?
        ORDER BY generated_at DESC
        """,
        (home_team, away_team),
    ).fetchall()
    _extend_matching_snapshot_rows(candidates, seen, rows, home_team, away_team)
    return _choose_timestamped_row_for_kickoff(candidates, "generated_at", kickoff_at, match_date)


def _extend_matching_snapshot_rows(
    target: list[sqlite3.Row],
    seen: set[str],
    rows: list[sqlite3.Row],
    home_team: str,
    away_team: str,
) -> None:
    for row in rows:
        row_id = str(row["id"])
        if row_id in seen or not _same_team_pair(row, home_team, away_team):
            continue
        target.append(row)
        seen.add(row_id)


def _same_team_pair(row: sqlite3.Row, home_team: str, away_team: str) -> bool:
    return _norm(row["home_team"]) == _norm(home_team) and _norm(row["away_team"]) == _norm(away_team)


def _choose_snapshot_for_kickoff(
    rows: list[sqlite3.Row],
    kickoff_at: str | None,
    match_date: str,
) -> sqlite3.Row | None:
    if not rows:
        return None
    kickoff_dt = _parse_dt(kickoff_at)
    if kickoff_dt is not None:
        before = [
            row for row in rows
            if (row_dt := _parse_dt(_as_optional_str(row["snapshot_at"]))) is not None
            and row_dt <= kickoff_dt
        ]
        if before:
            return before[0]
        return rows[0]
    return _latest_row_before_match(rows, "snapshot_at", match_date)


def _choose_timestamped_row_for_kickoff(
    rows: list[sqlite3.Row],
    timestamp_column: str,
    kickoff_at: str | None,
    match_date: str,
) -> sqlite3.Row | None:
    if not rows:
        return None
    kickoff_dt = _parse_dt(kickoff_at)
    if kickoff_dt is not None:
        before = [
            row for row in rows
            if (row_dt := _parse_dt(_as_optional_str(row[timestamp_column]))) is not None
            and row_dt <= kickoff_dt
        ]
        if before:
            return before[0]
        return rows[0]
    return _latest_row_before_match(rows, timestamp_column, match_date)


def _latest_row_before_match(
    rows: list[sqlite3.Row],
    timestamp_column: str,
    match_date: str,
) -> sqlite3.Row | None:
    match_dt = _parse_dt(match_date)
    if match_dt is None:
        return rows[0] if rows else None
    for row in rows:
        row_dt = _parse_dt(_as_optional_str(row[timestamp_column]))
        if row_dt is not None and row_dt <= match_dt:
            return row
    return None


def _has_process_eval(conn: sqlite3.Connection, match_id: str | None, schedule_id: str | None) -> bool:
    if not _has_table(conn, "postmatch_process_eval"):
        return False
    ids = [item for item in (match_id, schedule_id) if item]
    if not ids:
        return False
    placeholders = ",".join("?" for _ in ids)
    row = conn.execute(
        f"SELECT 1 FROM postmatch_process_eval WHERE CAST(match_id AS TEXT) IN ({placeholders}) LIMIT 1",
        ids,
    ).fetchone()
    return row is not None


def _current_probs_from_pre_snapshot(row: sqlite3.Row) -> dict[str, float] | None:
    raw = {
        "home": row["final_home_prob"],
        "draw": row["final_draw_prob"],
        "away": row["final_away_prob"],
    }
    try:
        home = float(raw["home"])
        draw = float(raw["draw"])
        away = float(raw["away"])
    except (TypeError, ValueError):
        return None
    if min(home, draw, away) < 0:
        return None
    total = home + draw + away
    if total <= 0:
        return None
    return {"home": home / total, "draw": draw / total, "away": away / total}


def _current_probs_from_prediction_snapshot(row: sqlite3.Row) -> dict[str, float] | None:
    for column_name in ("adjusted_probs", "baseline_probs"):
        parsed = _json_loads(_row_get(row, column_name))
        probs = _current_probs_from_mapping(parsed)
        if probs is not None:
            return probs
    return None


def _current_probs_from_mapping(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    try:
        home = float(raw.get("home", raw.get("home_win_prob", raw.get("home_prob"))))
        draw = float(raw.get("draw", raw.get("draw_prob")))
        away = float(raw.get("away", raw.get("away_win_prob", raw.get("away_prob"))))
    except (TypeError, ValueError):
        return None
    if min(home, draw, away) < 0:
        return None
    total = home + draw + away
    if total <= 0:
        return None
    return {"home": home / total, "draw": draw / total, "away": away / total}


def _probability_quality_issues(probs: dict[str, float] | None) -> list[str]:
    if probs is None:
        return ["missing_probabilities"]
    values = [float(probs[key]) for key in ("home", "draw", "away")]
    issues: list[str] = []
    if any(not math.isfinite(value) for value in values):
        issues.append("non_finite_probability")
    if any(value == 0.0 for value in values):
        issues.append("exact_zero_probability")
    if any(value == 1.0 for value in values):
        issues.append("exact_one_probability")
    return issues


def _model_cohort(model_version: str | None) -> str:
    text = str(model_version or "").strip()
    return text if text else "unknown"


def _should_use_prediction_snapshot_primary(
    *,
    pre_snapshot_at: str | None,
    pre_probs: dict[str, float] | None,
    prediction_snapshot_at: str | None,
    prediction_probs: dict[str, float] | None,
    kickoff_at: str | None,
) -> bool:
    """Prefer a clean prediction snapshot over an unusable pre-match snapshot."""
    prediction_clean = (
        prediction_probs is not None
        and _snapshot_before_kickoff(prediction_snapshot_at, kickoff_at) is True
    )
    if not prediction_clean:
        return False
    pre_clean = (
        pre_probs is not None
        and _snapshot_before_kickoff(pre_snapshot_at, kickoff_at) is True
    )
    return not pre_clean


def _sample_key(home_team: str, away_team: str, match_date: str) -> str:
    kickoff = _parse_dt(match_date)
    identity_time = kickoff.isoformat() if kickoff is not None else str(match_date)
    raw = f"{_norm(home_team)}::{_norm(away_team)}::{identity_time}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _norm(value: str) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def _json_loads(raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


def _component_count(raw: Any) -> int:
    if not isinstance(raw, dict):
        return 0
    return sum(1 for value in raw.values() if value)


def _valid_matrix(raw: Any) -> bool:
    return isinstance(raw, list) and bool(raw) and all(isinstance(row, list) for row in raw)


def _snapshot_before_kickoff(snapshot_at: str | None, kickoff_at: str | None) -> bool | None:
    snap = _parse_dt(snapshot_at)
    kickoff = _parse_dt(kickoff_at)
    if snap is None or kickoff is None:
        return None
    return snap <= kickoff


def _resolve_kickoff_at(
    match: sqlite3.Row | None,
    schedule: sqlite3.Row | None,
) -> tuple[str | None, str | None]:
    match_kickoff = _canonical_kickoff_at(_as_optional_str(match["match_date"]) if match else None)
    if match_kickoff:
        return match_kickoff, "matches.match_date"
    schedule_kickoff = _schedule_kickoff_at(schedule)
    if schedule_kickoff:
        return schedule_kickoff, "wc26_schedule.match_date+kickoff_time"
    return None, None


def _schedule_kickoff_at(schedule: sqlite3.Row | None) -> str | None:
    if schedule is None:
        return None
    match_date = _as_optional_str(schedule["match_date"])
    kickoff_time = _as_optional_str(schedule["kickoff_time"])
    if not match_date:
        return None
    if _canonical_kickoff_at(match_date):
        canonical = _canonical_kickoff_at(match_date)
        parsed = _parse_dt(canonical)
        return parsed.isoformat() if parsed is not None else canonical
    if len(match_date.strip()) != 10 or not kickoff_time:
        return None
    time_part = kickoff_time.strip()
    if len(time_part) == 5:
        time_part = f"{time_part}:00"
    if len(time_part) != 8:
        return None
    try:
        local_dt = datetime.fromisoformat(f"{match_date.strip()}T{time_part}")
    except ValueError:
        return None
    return local_dt.replace(tzinfo=SCHEDULE_TIMEZONE).isoformat()


def _canonical_kickoff_at(match_date: str | None) -> str | None:
    if not match_date:
        return None
    text = str(match_date).strip()
    if len(text) == 10:
        return None
    return text


def _horizon_hours(snapshot_at: str | None, kickoff_at: str | None) -> float | None:
    snap = _parse_dt(snapshot_at)
    kickoff = _parse_dt(kickoff_at)
    if snap is None or kickoff is None:
        return None
    return round((kickoff - snap).total_seconds() / 3600.0, 4)


def _horizon_bucket(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    if hours < 0:
        return "post_kickoff"
    if hours <= 1.5:
        return "T-90m"
    if hours <= 6:
        return "T-6h"
    if hours <= 24:
        return "T-24h"
    return "T-24h+"


def _leakage_status(
    *,
    snapshot_before_kickoff: bool | None,
    source_conflict: bool,
    has_snapshot: bool,
) -> str:
    if source_conflict:
        return "result_conflict"
    if not has_snapshot:
        return "no_pre_match_snapshot"
    if snapshot_before_kickoff is False:
        return "post_kickoff_snapshot"
    if snapshot_before_kickoff is None:
        return "unknown_time"
    return "clean"


def _sample_status(
    *,
    eligible: bool,
    source_conflict: bool,
    snapshot_before_kickoff: bool | None,
) -> str:
    if eligible:
        return "strict"
    if source_conflict or snapshot_before_kickoff is False:
        return "rejected"
    return "diagnostic"


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = str(raw).strip()
    if len(text) == 10:
        text = f"{text}T23:59:59+00:00"
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _completeness_score(**flags: bool) -> float:
    if not flags:
        return 0.0
    return round(sum(1 for value in flags.values() if value) / len(flags), 4)


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cohort_counts: dict[str, int] = {}
    strict_cohort_counts: dict[str, int] = {}
    for row in rows:
        cohort = str(row.get("model_cohort") or "unknown")
        cohort_counts[cohort] = cohort_counts.get(cohort, 0) + 1
        if row.get("sample_status") == "strict":
            strict_cohort_counts[cohort] = strict_cohort_counts.get(cohort, 0) + 1
    return {
        "total_samples": len(rows),
        "independent_match_count": len(rows),
        "canonical_result_count": sum(1 for row in rows if row["has_canonical_result"]),
        "match_results_count": sum(1 for row in rows if row["has_match_result"]),
        "schedule_finished_count": sum(1 for row in rows if row["has_schedule_result"]),
        "eligible_backtest_count": sum(1 for row in rows if row["eligible_for_backtest"]),
        "strict_count": sum(1 for row in rows if row["sample_status"] == "strict"),
        "diagnostic_count": sum(1 for row in rows if row["sample_status"] == "diagnostic"),
        "rejected_count": sum(1 for row in rows if row["sample_status"] == "rejected"),
        "with_pre_match_snapshot": sum(1 for row in rows if row["has_pre_match_snapshot"]),
        "with_prediction_snapshot": sum(1 for row in rows if row["has_prediction_snapshot"]),
        "with_process_eval": sum(1 for row in rows if row["has_process_eval"]),
        "source_result_conflicts": sum(1 for row in rows if row["source_result_conflict"]),
        "exact_zero_probability_count": sum(
            1 for row in rows
            if "exact_zero_probability" in row.get("probability_quality_issues", [])
        ),
        "strict_exact_zero_probability_count": sum(
            1 for row in rows
            if row.get("sample_status") == "strict"
            and "exact_zero_probability" in row.get("probability_quality_issues", [])
        ),
        "model_cohort_counts": dict(sorted(cohort_counts.items())),
        "strict_model_cohort_counts": dict(sorted(strict_cohort_counts.items())),
        "schedule_only_finished_count": sum(
            1 for row in rows if row["has_schedule_result"] and not row["has_match_result"]
        ),
    }


def _stable_hash(payload: Any) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    if not _has_table(conn, table_name):
        return False
    return any(row["name"] == column_name for row in conn.execute(f"PRAGMA table_info({table_name})"))


def _row_get(row: sqlite3.Row, column_name: str) -> Any:
    return row[column_name] if column_name in row.keys() else None


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
