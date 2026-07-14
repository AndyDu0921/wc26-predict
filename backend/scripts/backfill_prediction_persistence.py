"""Backfill missing prediction persistence records from original snapshots.

This script repairs closed-loop persistence gaps without re-running a
prediction. It copies already captured pre-match evidence into the downstream
tables that post-match evaluation and self-learning expect.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent
DEFAULT_DB_PATH = BACKEND_DIR / "data" / "local_stage2.db"
DEFAULT_MATCH_IDS = ("197", "198", "199", "200")


def _json_load(raw: Any, default: Any = None) -> Any:
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _json_dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _match_id_keys(match_id: str) -> list[str]:
    raw = str(match_id)
    keys = [raw]
    try:
        parsed = uuid.UUID(raw)
    except ValueError:
        return keys
    keys.extend([parsed.hex, str(parsed)])
    return list(dict.fromkeys(keys))


def _in_clause(values: list[str]) -> tuple[str, list[str]]:
    return ", ".join("?" for _ in values), values


def _prob_payload(home: float, draw: float, away: float) -> dict[str, float]:
    return {
        "home": float(home),
        "draw": float(draw),
        "away": float(away),
        "home_win_prob": float(home),
        "draw_prob": float(draw),
        "away_win_prob": float(away),
    }


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def _has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _latest_pre_match_snapshot(conn: sqlite3.Connection, match_id: str) -> sqlite3.Row | None:
    keys = _match_id_keys(match_id)
    placeholders, params = _in_clause(keys)
    return conn.execute(
        f"""
        SELECT *
        FROM pre_match_snapshots
        WHERE CAST(match_id AS TEXT) IN ({placeholders})
        ORDER BY snapshot_at DESC, id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()


def _count(conn: sqlite3.Connection, table_name: str, match_id: str) -> int:
    keys = _match_id_keys(match_id)
    placeholders, params = _in_clause(keys)
    if table_name == "postmatch_eval":
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS c
            FROM postmatch_eval pe
            JOIN prediction_runs pr ON pe.prediction_run_id = pr.id
            WHERE CAST(pr.match_id AS TEXT) IN ({placeholders})
            """,
            params,
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM {table_name} WHERE CAST(match_id AS TEXT) IN ({placeholders})",
            params,
        ).fetchone()
    return int(row["c"] if row else 0)


def _prediction_run(conn: sqlite3.Connection, match_id: str) -> sqlite3.Row | None:
    keys = _match_id_keys(match_id)
    placeholders, params = _in_clause(keys)
    return conn.execute(
        f"""
        SELECT *
        FROM prediction_runs
        WHERE CAST(match_id AS TEXT) IN ({placeholders})
        ORDER BY as_of_time DESC, created_at DESC
        LIMIT 1
        """,
        params,
    ).fetchone()


def _prediction_snapshot(conn: sqlite3.Connection, match_id: str) -> sqlite3.Row | None:
    keys = _match_id_keys(match_id)
    placeholders, params = _in_clause(keys)
    return conn.execute(
        f"""
        SELECT *
        FROM prediction_snapshots
        WHERE CAST(match_id AS TEXT) IN ({placeholders})
        ORDER BY generated_at DESC, id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()


def _report_for(home_team: str, away_team: str) -> tuple[str | None, str | None]:
    reports_dir = REPO_DIR / "reports" / "predictions"
    if not reports_dir.exists():
        return None, None
    home_token = home_team.replace(" ", "_")
    away_token = away_team.replace(" ", "_")
    candidates = sorted(
        reports_dir.glob(f"*_{home_token}_vs_{away_token}_prediction.md"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None, None
    path = candidates[0]
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError:
        markdown = None
    return str(path.relative_to(REPO_DIR)), markdown


def _approved_signal_ids(snapshot: sqlite3.Row) -> list[str]:
    ids = _json_load(snapshot["news_signal_ids"], [])
    if not isinstance(ids, list):
        return []
    return [str(item) for item in ids if item]


def _approved_signal_payloads(
    conn: sqlite3.Connection,
    signal_ids: list[str],
) -> list[dict[str, Any]]:
    if not signal_ids:
        return []
    placeholders = ",".join("?" for _ in signal_ids)
    rows = conn.execute(
        f"""
        SELECT ns.id, t.name AS team, ns.signal_type, ns.impact_direction,
               ns.evidence_id, ns.confidence, ns.summary_zh
        FROM news_signals ns
        LEFT JOIN teams t ON t.id = ns.team_id
        WHERE ns.id IN ({placeholders})
        """,
        signal_ids,
    ).fetchall()
    by_id = {str(row["id"]): row for row in rows}
    payloads: list[dict[str, Any]] = []
    for signal_id in signal_ids:
        row = by_id.get(signal_id)
        if row is None:
            payloads.append({"id": signal_id, "source_status": "missing_news_signal"})
            continue
        payloads.append(
            {
                "id": signal_id,
                "team": row["team"],
                "signal_type": row["signal_type"],
                "impact_direction": row["impact_direction"],
                "evidence_id": row["evidence_id"],
                "confidence": row["confidence"],
                "summary_zh": row["summary_zh"],
            }
        )
    return payloads


def _risk_tags(snapshot: sqlite3.Row) -> list[str]:
    tags = _json_load(snapshot["risk_tags"], [])
    if not isinstance(tags, list):
        return []
    return [str(item) for item in tags]


def _top_scores(snapshot: sqlite3.Row) -> list[dict[str, Any]]:
    top_scores = _json_load(snapshot["top_scores"], [])
    return top_scores if isinstance(top_scores, list) else []


def _matrix(snapshot: sqlite3.Row, column: str) -> list[list[float]] | None:
    payload = _json_load(snapshot[column], None)
    if isinstance(payload, list):
        return payload
    return None


def _market_probs(snapshot: sqlite3.Row) -> dict[str, float] | None:
    odds = _json_load(snapshot["odds_snapshot"], {})
    if not isinstance(odds, dict):
        return None
    home = odds.get("home", odds.get("home_prob", odds.get("home_win_prob")))
    draw = odds.get("draw", odds.get("draw_prob"))
    away = odds.get("away", odds.get("away_prob", odds.get("away_win_prob")))
    if home is None or draw is None or away is None:
        return None
    return _prob_payload(float(home), float(draw), float(away))


def _component_probs(snapshot: sqlite3.Row) -> dict[str, Any]:
    payload = _json_load(snapshot["component_probs"], {})
    if not isinstance(payload, dict):
        return {}
    normalized = dict(payload)
    if "dc" not in normalized and "dixon_coles" in normalized:
        normalized["dc"] = normalized["dixon_coles"]
    if "pi" not in normalized and "pi_rating" in normalized:
        normalized["pi"] = normalized["pi_rating"]
    market = _market_probs(snapshot)
    if market and "market" not in normalized:
        normalized["market"] = market
    return normalized


def _pipeline_params(snapshot: sqlite3.Row) -> dict[str, Any]:
    fields = [
        "weight_config_label",
        "weight_config",
        "effective_weights",
        "fusion_graph",
        "model_disagreement",
        "market_blended",
        "market_weight_used",
        "market_divergence",
        "confidence_penalty",
        "pipeline_status",
        "degraded_reasons",
        "code_version",
        "data_fingerprint",
        "prediction_mode",
        "source_timestamps",
        "odds_snapshot_id",
        "weather_snapshot_id",
        "injury_snapshot_id",
        "fused_score_matrix",
        "source_score_matrices",
    ]
    params: dict[str, Any] = {
        "backfill_source": "pre_match_snapshots",
        "source_snapshot_id": snapshot["id"],
    }
    for field in fields:
        value = snapshot[field]
        decoded = _json_load(value, value)
        if decoded is not None:
            params[field] = decoded
    return params


def _feature_snapshot(snapshot: sqlite3.Row, approved_signals: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "backfill_source": "pre_match_snapshots",
        "source_snapshot_id": snapshot["id"],
        "snapshot_at": snapshot["snapshot_at"],
        "kickoff_at": snapshot["kickoff_at"],
        "weather_available": bool(snapshot["weather_available"]),
        "odds_available": bool(snapshot["odds_available"]),
        "lineup_available": bool(snapshot["lineup_available"]),
        "injury_data_available": bool(snapshot["injury_data_available"]),
        "news_signals_available": bool(snapshot["news_signals_available"]),
        "risk_tags": _risk_tags(snapshot),
        "approved_signal_ids": [item.get("id") for item in approved_signals],
        "data_fingerprint": snapshot["data_fingerprint"],
        "model_version": snapshot["model_version"] or snapshot["code_version"],
    }


def _confidence_score(confidence: str | None) -> float:
    return {
        "high": 0.80,
        "medium": 0.65,
        "low": 0.45,
    }.get(str(confidence or "").lower(), 0.55)


def _insert_dynamic(
    conn: sqlite3.Connection,
    table_name: str,
    values: dict[str, Any],
) -> None:
    columns = _table_columns(conn, table_name)
    filtered = {key: value for key, value in values.items() if key in columns}
    names = list(filtered)
    placeholders = ", ".join("?" for _ in names)
    conn.execute(
        f"INSERT INTO {table_name} ({', '.join(names)}) VALUES ({placeholders})",
        [filtered[name] for name in names],
    )


def _prediction_snapshot_values(
    conn: sqlite3.Connection,
    snapshot: sqlite3.Row,
) -> dict[str, Any]:
    probs = _prob_payload(
        float(snapshot["final_home_prob"]),
        float(snapshot["final_draw_prob"]),
        float(snapshot["final_away_prob"]),
    )
    report_path, report_md = _report_for(snapshot["home_team"], snapshot["away_team"])
    values: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "match_id": str(snapshot["match_id"]),
        "generated_at": snapshot["snapshot_at"],
        "model_version": snapshot["model_version"] or snapshot["code_version"] or "unknown",
        "run_type": snapshot["prediction_mode"] or "full",
        "home_team": snapshot["home_team"],
        "away_team": snapshot["away_team"],
        "competition": snapshot["competition"],
        "match_time": snapshot["kickoff_at"],
        "baseline_probs": _json_dump(probs),
        "market_probs": _json_dump(_market_probs(snapshot)),
        "adjusted_probs": _json_dump(probs),
        "expected_goals": _json_dump(
            {"home": snapshot["home_xg"], "away": snapshot["away_xg"]}
        ),
        "top_scores": _json_dump(_top_scores(snapshot)),
        "elo_ratings": _json_dump({}),
        "active_event_ids": _json_dump(_approved_signal_ids(snapshot)),
        "missing_inputs": _json_dump(_json_load(snapshot["missing_inputs"], [])),
        "confidence": snapshot["confidence"],
        "calibration_monitor": _json_dump({}),
        "pipeline_params": _json_dump(_pipeline_params(snapshot)),
        "report_path": report_path,
        "report_markdown": report_md,
        "component_probs": _json_dump(_component_probs(snapshot)),
        "fused_score_matrix": _json_dump(_matrix(snapshot, "fused_score_matrix")),
        "source_score_matrices": _json_dump(_json_load(snapshot["source_score_matrices"], {})),
    }
    return {key: value for key, value in values.items() if key in _table_columns(conn, "prediction_snapshots")}


def _prediction_run_values(
    snapshot: sqlite3.Row,
    approved_signals: list[dict[str, Any]],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    score_matrix = _matrix(snapshot, "fused_score_matrix") or []
    return {
        "id": run_id or str(uuid.uuid4()),
        "match_id": str(snapshot["match_id"]),
        "run_type": snapshot["prediction_mode"] or "full",
        "model_version": snapshot["model_version"] or snapshot["code_version"] or "unknown",
        "as_of_time": snapshot["snapshot_at"],
        "home_win_prob": float(snapshot["final_home_prob"]),
        "draw_prob": float(snapshot["final_draw_prob"]),
        "away_win_prob": float(snapshot["final_away_prob"]),
        "home_xg": float(snapshot["home_xg"]),
        "away_xg": float(snapshot["away_xg"]),
        "score_matrix": _json_dump(score_matrix),
        "top3_scores": _json_dump(_top_scores(snapshot)),
        "confidence_score": _confidence_score(snapshot["confidence"]),
        "risk_tags": _json_dump(_risk_tags(snapshot)),
        "input_feature_snapshot": _json_dump(_feature_snapshot(snapshot, approved_signals)),
        "approved_signals": _json_dump(approved_signals),
        "created_at": snapshot["snapshot_at"],
    }


def _schedule_result(conn: sqlite3.Connection, match_id: str) -> sqlite3.Row | None:
    keys = _match_id_keys(match_id)
    placeholders, params = _in_clause(keys)
    return conn.execute(
        f"""
        SELECT id, home_team, away_team, home_goals, away_goals, match_status
        FROM wc26_schedule
        WHERE CAST(id AS TEXT) IN ({placeholders})
        """,
        params,
    ).fetchone()


def _team_id(conn: sqlite3.Connection, team_name: str | None) -> str | None:
    if not team_name:
        return None
    row = conn.execute("SELECT id FROM teams WHERE name = ? LIMIT 1", (team_name,)).fetchone()
    return str(row["id"]) if row is not None else None


def _ensure_matches_parent(
    conn: sqlite3.Connection,
    snapshot: sqlite3.Row,
    *,
    persist: bool,
) -> dict[str, Any] | None:
    if not (_has_table(conn, "matches") and _has_table(conn, "teams") and _has_table(conn, "wc26_schedule")):
        return None
    match_id = str(snapshot["match_id"])
    keys = _match_id_keys(match_id)
    placeholders, params = _in_clause(keys)
    exists = conn.execute(
        f"SELECT 1 FROM matches WHERE CAST(id AS TEXT) IN ({placeholders}) LIMIT 1",
        params,
    ).fetchone()
    if exists is not None:
        return None

    schedule = conn.execute(
        """
        SELECT id, match_number, home_team, away_team, stage, match_date,
               kickoff_time, venue, city, match_status
        FROM wc26_schedule
        WHERE CAST(id AS TEXT) IN ({placeholders})
        """,
        params,
    ).fetchone()
    if schedule is None:
        return None

    home_team_id = _team_id(conn, schedule["home_team"] or snapshot["home_team"])
    away_team_id = _team_id(conn, schedule["away_team"] or snapshot["away_team"])
    if home_team_id is None or away_team_id is None:
        return {
            "match_id": match_id,
            "action": "skip_insert_matches_parent",
            "reason": "team_not_found",
        }

    columns = _table_columns(conn, "matches")
    now = datetime.now(timezone.utc).isoformat()
    values = {
        "id": match_id,
        "external_id": f"wc26_schedule:{match_id}",
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "match_date": snapshot["kickoff_at"] or f"{schedule['match_date']}T{schedule['kickoff_time']}:00",
        "competition": snapshot["competition"] or "FIFA World Cup 2026",
        "competition_weight": 1.0,
        "stage": schedule["stage"],
        "venue": schedule["venue"],
        "is_neutral_venue": 1,
        "status": str(schedule["match_status"] or "scheduled").lower(),
        "created_at": now,
        "updated_at": now,
        "competition_type": "national",
    }
    filtered = {key: value for key, value in values.items() if key in columns}
    if persist:
        names = list(filtered)
        conn.execute(
            f"INSERT INTO matches ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
            [filtered[name] for name in names],
        )
    return {
        "match_id": match_id,
        "action": "insert_matches_parent",
        "home_team": schedule["home_team"],
        "away_team": schedule["away_team"],
    }


def _postmatch_eval_values(
    prediction_run_id: str,
    run_values: dict[str, Any] | sqlite3.Row,
    schedule_row: sqlite3.Row,
    snapshot_id: str,
) -> dict[str, Any]:
    home_goals = int(schedule_row["home_goals"])
    away_goals = int(schedule_row["away_goals"])
    actual_idx = 0 if home_goals > away_goals else 1 if home_goals == away_goals else 2
    actual = [0.0, 0.0, 0.0]
    actual[actual_idx] = 1.0
    probs = [
        float(run_values["home_win_prob"]),
        float(run_values["draw_prob"]),
        float(run_values["away_win_prob"]),
    ]
    brier = sum((prob - obs) ** 2 for prob, obs in zip(probs, actual, strict=False))
    log_loss = -math.log(max(probs[actual_idx], 1e-12))
    top3 = _json_load(run_values["top3_scores"], [])
    actual_score = f"{home_goals}:{away_goals}"
    exact_hit = bool(top3 and top3[0].get("score") == actual_score)
    top3_hit = any(item.get("score") == actual_score for item in top3 if isinstance(item, dict))
    bucket = min(10, max(1, int(max(probs) * 10) + 1))
    notes = {
        "backfill_source": "pre_match_snapshots",
        "source_snapshot_id": snapshot_id,
        "probabilities_changed": False,
        "reason": "repair_missing_postmatch_eval_for_closed_loop",
    }
    return {
        "id": str(uuid.uuid4()),
        "prediction_run_id": prediction_run_id,
        "actual_home_goals": home_goals,
        "actual_away_goals": away_goals,
        "actual_result": ["H", "D", "A"][actual_idx],
        "brier_score": brier,
        "log_loss": log_loss,
        "exact_score_hit": int(exact_hit),
        "top3_hit": int(top3_hit),
        "calibration_bucket": bucket,
        "notes": _json_dump(notes),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _normalize_existing_signals(
    conn: sqlite3.Connection,
    run: sqlite3.Row,
    snapshot: sqlite3.Row,
) -> list[dict[str, Any]] | None:
    current = _json_load(run["approved_signals"], [])
    if isinstance(current, list) and all(isinstance(item, dict) for item in current):
        return None
    signal_ids = [str(item) for item in current if isinstance(item, str)]
    if not signal_ids:
        signal_ids = _approved_signal_ids(snapshot)
    return _approved_signal_payloads(conn, signal_ids)


def repair_match(
    conn: sqlite3.Connection,
    match_id: str,
    *,
    persist: bool,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    snapshot = _latest_pre_match_snapshot(conn, match_id)
    if snapshot is None:
        return [{"match_id": match_id, "action": "skip", "reason": "missing_pre_match_snapshot"}]

    approved_signals = _approved_signal_payloads(conn, _approved_signal_ids(snapshot))

    parent_action = _ensure_matches_parent(conn, snapshot, persist=persist)
    if parent_action is not None:
        actions.append(parent_action)

    if _count(conn, "prediction_snapshots", match_id) == 0:
        values = _prediction_snapshot_values(conn, snapshot)
        actions.append(
            {
                "match_id": match_id,
                "action": "insert_prediction_snapshot",
                "snapshot_id": values["id"],
                "source_snapshot_id": snapshot["id"],
            }
        )
        if persist:
            _insert_dynamic(conn, "prediction_snapshots", values)
    else:
        existing_snapshot = _prediction_snapshot(conn, match_id)
        snapshot_columns = _table_columns(conn, "prediction_snapshots")
        updates: dict[str, str] = {}
        if (
            existing_snapshot is not None
            and "fused_score_matrix" in snapshot_columns
            and existing_snapshot["fused_score_matrix"] in (None, "")
        ):
            updates["fused_score_matrix"] = _json_dump(_matrix(snapshot, "fused_score_matrix"))
        if (
            existing_snapshot is not None
            and "source_score_matrices" in snapshot_columns
            and existing_snapshot["source_score_matrices"] in (None, "")
        ):
            updates["source_score_matrices"] = _json_dump(
                _json_load(snapshot["source_score_matrices"], {})
            )
        if updates:
            actions.append(
                {
                    "match_id": match_id,
                    "action": "update_prediction_snapshot_score_matrices",
                    "prediction_snapshot_id": existing_snapshot["id"],
                    "columns": sorted(updates),
                }
            )
            if persist:
                assignments = ", ".join(f"{column} = ?" for column in updates)
                conn.execute(
                    f"UPDATE prediction_snapshots SET {assignments} WHERE id = ?",
                    [*updates.values(), existing_snapshot["id"]],
                )

    run = _prediction_run(conn, match_id)
    run_values: dict[str, Any] | sqlite3.Row
    if run is None:
        run_id = str(uuid.uuid4())
        run_values = _prediction_run_values(snapshot, approved_signals, run_id=run_id)
        actions.append(
            {
                "match_id": match_id,
                "action": "insert_prediction_run",
                "prediction_run_id": run_id,
                "source_snapshot_id": snapshot["id"],
            }
        )
        if persist:
            _insert_dynamic(conn, "prediction_runs", run_values)
    else:
        run_values = run
        normalized_signals = _normalize_existing_signals(conn, run, snapshot)
        if normalized_signals is not None:
            actions.append(
                {
                    "match_id": match_id,
                    "action": "update_approved_signals",
                    "prediction_run_id": run["id"],
                    "signal_count": len(normalized_signals),
                }
            )
            if persist:
                conn.execute(
                    "UPDATE prediction_runs SET approved_signals = ? WHERE id = ?",
                    (_json_dump(normalized_signals), run["id"]),
                )

    prediction_run_id = str(run_values["id"])

    keys = _match_id_keys(match_id)
    placeholders, params = _in_clause(keys)
    null_logs = conn.execute(
        f"""
        SELECT id
        FROM prediction_learning_log
        WHERE CAST(match_id AS TEXT) IN ({placeholders})
          AND (prediction_run_id IS NULL OR prediction_run_id = '')
        """,
        params,
    ).fetchall()
    if null_logs:
        actions.append(
            {
                "match_id": match_id,
                "action": "link_learning_log",
                "prediction_run_id": prediction_run_id,
                "row_count": len(null_logs),
            }
        )
        if persist:
            conn.execute(
                f"""
                UPDATE prediction_learning_log
                SET prediction_run_id = ?
                WHERE CAST(match_id AS TEXT) IN ({placeholders})
                  AND (prediction_run_id IS NULL OR prediction_run_id = '')
                """,
                [prediction_run_id, *params],
            )

    schedule = _schedule_result(conn, match_id)
    finished = (
        schedule is not None
        and str(schedule["match_status"]).upper() == "FINISHED"
        and schedule["home_goals"] is not None
        and schedule["away_goals"] is not None
    )
    if finished and _count(conn, "postmatch_eval", match_id) == 0:
        eval_values = _postmatch_eval_values(prediction_run_id, run_values, schedule, snapshot["id"])
        actions.append(
            {
                "match_id": match_id,
                "action": "insert_postmatch_eval",
                "prediction_run_id": prediction_run_id,
                "brier_score": round(float(eval_values["brier_score"]), 6),
                "log_loss": round(float(eval_values["log_loss"]), 6),
            }
        )
        if persist:
            _insert_dynamic(conn, "postmatch_eval", eval_values)

    if not actions:
        actions.append({"match_id": match_id, "action": "noop"})
    return actions


def summarize_counts(conn: sqlite3.Connection, match_ids: list[str]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for match_id in match_ids:
        summary[match_id] = {
            "pre_match_snapshots": _count(conn, "pre_match_snapshots", match_id),
            "prediction_snapshots": _count(conn, "prediction_snapshots", match_id),
            "prediction_runs": _count(conn, "prediction_runs", match_id),
            "prediction_learning_log": _count(conn, "prediction_learning_log", match_id),
            "postmatch_eval": _count(conn, "postmatch_eval", match_id),
        }
    return summary


def action_hash(actions: list[dict[str, Any]]) -> str:
    stable = [
        {
            key: value
            for key, value in action.items()
            if key not in {"snapshot_id", "prediction_run_id"}
        }
        for action in actions
    ]
    payload = _json_dump(stable)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def backup_db(db_path: Path) -> Path:
    backup_dir = db_path.parent
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{db_path.stem}.before_prediction_persistence_backfill.{stamp}{db_path.suffix}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--match-ids", nargs="+", default=list(DEFAULT_MATCH_IDS))
    parser.add_argument("--dry-run", action="store_true", help="Preview only.")
    parser.add_argument("--persist", action="store_true", help="Write the repair.")
    parser.add_argument("--no-backup", action="store_true", help="Do not backup before --persist.")
    args = parser.parse_args(argv)

    persist = bool(args.persist)
    if args.dry_run and persist:
        parser.error("--dry-run and --persist are mutually exclusive")

    db_path = Path(args.db_path)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    match_ids = [str(item) for item in args.match_ids]
    backup_path = None
    if persist and not args.no_backup:
        backup_path = backup_db(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    before = summarize_counts(conn, match_ids)
    actions: list[dict[str, Any]] = []
    try:
        with conn:
            for match_id in match_ids:
                actions.extend(repair_match(conn, match_id, persist=persist))
    finally:
        after = summarize_counts(conn, match_ids)
        conn.close()

    result = {
        "mode": "persist" if persist else "dry_run",
        "db_path": str(db_path),
        "backup_path": str(backup_path) if backup_path else None,
        "match_ids": match_ids,
        "action_hash": action_hash(actions),
        "before": before,
        "actions": actions,
        "after": after,
    }
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
