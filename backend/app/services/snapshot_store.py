"""Save prediction results as standardized PredictionSnapshot AND PredictionRun records.

Writes to both prediction_snapshots (script-side) and prediction_runs (API-side)
so that RPS optimizer and postmatch eval can find data regardless of entry point.
"""

from __future__ import annotations

import math
import uuid as _uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import numpy as np

import logging

from app.database import AsyncSessionLocal
from app.models.prediction_snapshot import PredictionSnapshot
from app.version import VERSION
from app.services.evaluation_sample import evaluation_sample_from_prediction_dict, normalize_1x2_payload


async def save_prediction_snapshot(
    result: dict[str, Any],
    run_type: str = "baseline_v0",
    report_path: str | None = None,
    report_markdown: str | None = None,
) -> PredictionSnapshot:
    """Persist a prediction result as a standardized snapshot.

    Also writes a PredictionRun record so the RPS optimizer (optimize_weights.py)
    and Celery postmatch_eval tasks can find this prediction via prediction_runs.
    """
    result = _normalize_prediction_result(result)
    m = result["meta"]
    p = result["prediction"]
    e = result["elo"]
    match_id_raw = _resolve_or_require_match_id(m)
    m["match_id"] = match_id_raw
    result["evaluation_sample"] = evaluation_sample_from_prediction_dict(result)
    evaluation_sample = result["evaluation_sample"]
    candidate_probs = evaluation_sample.get("candidate_probs", {})
    pipeline = result.get("pipeline") or result.get("pipeline_params") or {}

    # Determine confidence level
    missing_count = len(result.get("missing_inputs", []))
    if missing_count <= 1:
        confidence = "medium"
    elif missing_count <= 3:
        confidence = "low"
    else:
        confidence = "low"

    # Rebuild score matrix from xG for prediction_runs compatibility
    score_matrix = _build_score_matrix(p["home_xg"], p["away_xg"])
    conf_score = 0.55
    cal_monitor = result.get("calibration_monitor", {})
    if cal_monitor and cal_monitor.get("baseline_probs"):
        conf_score = 0.65
    baseline_probs = candidate_probs.get("snapshot_baseline") or candidate_probs.get("current_fusion")
    adjusted_probs = candidate_probs.get("snapshot_adjusted") or candidate_probs.get("current_fusion")

    snapshot = PredictionSnapshot(
        match_id=match_id_raw,
        run_type=run_type,
        model_version=VERSION,
        home_team=m["home_team"],
        away_team=m["away_team"],
        competition=m["competition"],
        match_time=m.get("match_date", None),
        baseline_probs=baseline_probs,
        component_probs=result.get("component_probs"),
        market_probs=_extract_market_probs(result),
        adjusted_probs=adjusted_probs,
        expected_goals={
            "home": p["home_xg"],
            "away": p["away_xg"],
        },
        top_scores=p.get("top3_scores", []),
        elo_ratings={
            "home": e["home_elo"],
            "away": e["away_elo"],
            "gap": e["rating_gap"],
            "k_factor": e["k_factor"],
        },
        active_event_ids=[],
        missing_inputs=[str(item) for item in result.get("missing_inputs", [])],
        confidence=confidence,
        calibration_monitor=cal_monitor,
        pipeline_params=_build_snapshot_pipeline_params(
            pipeline,
            m,
            p,
            evaluation_sample,
        ),
        report_path=report_path,
        report_markdown=report_markdown,
    )

    async with AsyncSessionLocal() as db:
        db.add(snapshot)
        await db.commit()
        await db.refresh(snapshot)

    # Sync to prediction_runs (bridge for RPS optimizer + Celery tasks)
    await _sync_to_prediction_runs(result, run_type, score_matrix, conf_score)

    return snapshot


async def _sync_to_prediction_runs(
    result: dict[str, Any],
    run_type: str,
    score_matrix: list[list[float]],
    confidence_score: float,
) -> None:
    """Write a matching PredictionRun record so optimize_weights.py finds this prediction.

    Uses raw SQL to avoid ORM UUID conversion issues with CHAR(32) match_id format.
    """
    m = result["meta"]
    p = result["prediction"]
    adj = result.get("adjustment", {})
    risk_tags = adj.get("risk_tags") or p.get("risk_tags", [])
    adjustment_log = adj.get("log", [])

    # Normalize match_id to UUID format (add dashes if missing)
    match_id_raw = _resolve_or_require_match_id(m)
    match_uuid = _normalize_uuid(match_id_raw)
    if match_uuid is None:
        raise ValueError(f"Cannot sync prediction run with invalid match_id={match_id_raw!r}")

    # Map run_type string to PredictionRunType enum value
    run_type_map = {
        "baseline_v0": "manual",
        "manual": "manual",
        "t_minus_24h": "t_minus_24h",
        "t_minus_3h": "t_minus_3h",
        "t_lineup": "lineup_confirmed",
    }
    prt_value = run_type_map.get(run_type, "manual")

    evaluation_sample = result.get("evaluation_sample") or evaluation_sample_from_prediction_dict(result)
    approved_signals = _collect_approved_signal_payloads(result, adjustment_log)

    now_ts = datetime.now(timezone.utc).isoformat()
    as_of_ts = str(m.get("as_of") or m.get("generated_at") or now_ts)
    pred_run_id = str(_uuid.uuid4())

    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as db:
            approved_signals = await _hydrate_approved_signals_from_db(approved_signals, db)
            feature_snapshot = _build_prediction_run_feature_snapshot(
                result,
                adjustment_log,
                evaluation_sample,
                approved_signals,
            )
            await db.execute(
                text(
                    "INSERT INTO prediction_runs "
                    "(id, match_id, run_type, model_version, as_of_time, "
                    " home_win_prob, draw_prob, away_win_prob, home_xg, away_xg, "
                    " score_matrix, top3_scores, confidence_score, risk_tags, "
                    " input_feature_snapshot, approved_signals, created_at) "
                    "VALUES ("
                    " :id, :match_id, :run_type, :model_version, :as_of_time, "
                    " :home_win_prob, :draw_prob, :away_win_prob, :home_xg, :away_xg, "
                    " :score_matrix, :top3_scores, :confidence_score, :risk_tags, "
                    " :feature_snapshot, :approved_signals, :created_at"
                    ")"
                ),
                {
                    "id": pred_run_id,
                    "match_id": match_uuid,
                    "run_type": prt_value,
                    "model_version": VERSION,
                    "as_of_time": as_of_ts,
                    "home_win_prob": round(float(p["home_win_prob"]), 6),
                    "draw_prob": round(float(p["draw_prob"]), 6),
                    "away_win_prob": round(float(p["away_win_prob"]), 6),
                    "home_xg": round(float(p["home_xg"]), 4),
                    "away_xg": round(float(p["away_xg"]), 4),
                    "score_matrix": _json_dumps(score_matrix),
                    "top3_scores": _json_dumps(p.get("top3_scores", [])),
                    "confidence_score": round(float(confidence_score), 4),
                    "risk_tags": _json_dumps(risk_tags),
                    "feature_snapshot": _json_dumps(feature_snapshot),
                    "approved_signals": _json_dumps(approved_signals),
                    "created_at": now_ts,
                },
            )
            await db.commit()
    except Exception as exc:
        # prediction_runs sync is best-effort; never break the main pipeline
        logging.getLogger(__name__).warning(
            "_sync_to_prediction_runs failed for %s vs %s: %s",
            m.get("home_team", "?"), m.get("away_team", "?"), exc
        )


def _normalize_uuid(raw: str) -> str | None:
    """Convert a 32-char hex string to UUID format with dashes."""
    if not raw:
        return None
    clean = raw.replace("-", "").strip()
    if len(clean) != 32:
        return None
    return f"{clean[:8]}-{clean[8:12]}-{clean[12:16]}-{clean[16:20]}-{clean[20:]}"


def _require_match_id(raw: Any) -> str:
    """Return a non-empty UUID-like match id or raise before saving."""
    match_id = str(raw or "").strip()
    if not match_id:
        raise ValueError("Prediction snapshots require a real match_id")
    if _normalize_uuid(match_id) is None:
        raise ValueError(f"Prediction snapshots require a UUID-like match_id, got {match_id!r}")
    return match_id


def _resolve_or_require_match_id(meta: dict[str, Any]) -> str:
    """Resolve match id from metadata before enforcing the closed-loop gate."""
    raw = str(meta.get("match_id") or "").strip()
    if _normalize_uuid(raw) is not None:
        return raw
    try:
        from app.services.match_resolver import resolve_match_id

        resolved = resolve_match_id(
            home_team=str(meta.get("home_team") or ""),
            away_team=str(meta.get("away_team") or ""),
            competition=str(meta.get("competition") or ""),
            kickoff_at=str(meta.get("match_date") or ""),
            stage=str(meta.get("stage") or ""),
        )
        if resolved:
            return resolved.match_id
    except Exception:
        logging.getLogger(__name__).debug("match_id resolver skipped", exc_info=True)
    return _require_match_id(raw)


def _normalize_prediction_result(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy/canonical result keys at the persistence boundary."""
    normalized = deepcopy(result)
    prediction = normalized.setdefault("prediction", {})
    elo = normalized.setdefault("elo", {})

    if "top3_scores" not in prediction and "top_scores" in prediction:
        prediction["top3_scores"] = prediction.get("top_scores") or []
    if "top_scores" not in prediction and "top3_scores" in prediction:
        prediction["top_scores"] = prediction.get("top3_scores") or []

    if "rating_gap" not in elo and "elo_gap" in elo:
        elo["rating_gap"] = elo.get("elo_gap")
    if "k_factor" not in elo:
        detail = elo.get("detail") or {}
        elo["k_factor"] = detail.get("k_factor", 0.0)

    missing_inputs = normalized.get("missing_inputs")
    if missing_inputs is None:
        missing_inputs = []
        for item in normalized.get("missing_data", []):
            if isinstance(item, dict):
                missing_inputs.append(str(item.get("item", item)))
            else:
                missing_inputs.append(str(item))
    normalized["missing_inputs"] = missing_inputs
    normalized["evaluation_sample"] = evaluation_sample_from_prediction_dict(normalized)
    return normalized


def _extract_market_probs(result: dict[str, Any]) -> dict[str, float] | None:
    """Persist market only when all three probabilities are present."""
    evaluation_sample = result.get("evaluation_sample") or {}
    candidate_probs = evaluation_sample.get("candidate_probs") or {}
    market_from_sample = normalize_1x2_payload(candidate_probs.get("market_only"))
    if market_from_sample:
        return market_from_sample
    component_market = (result.get("component_probs") or {}).get("market")
    return normalize_1x2_payload(component_market)


def _collect_approved_signal_payloads(
    result: dict[str, Any],
    adjustment_log: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Collect model-used signal evidence from legacy and canonical payloads."""
    seen: set[str] = set()
    collected: list[dict[str, Any]] = []

    def add_many(value: Any) -> None:
        if not value:
            return
        values = value if isinstance(value, list) else [value]
        for item in values:
            normalized = _normalize_approved_signal_payload(item)
            if not normalized:
                continue
            signal_id = str(normalized.get("id") or "").strip()
            if not signal_id or signal_id in seen:
                continue
            seen.add(signal_id)
            collected.append(normalized)

    prediction = result.get("prediction") or {}
    meta = result.get("meta") or {}
    pipeline = result.get("pipeline") or result.get("pipeline_params") or {}
    adjustment = result.get("adjustment") or {}

    for container in (result, prediction, meta, pipeline, adjustment):
        if isinstance(container, dict):
            add_many(container.get("approved_signals"))
            add_many(container.get("news_signals"))
            add_many(container.get("news_signal_ids"))

    add_many(result.get("signals"))
    add_many(result.get("active_events"))
    add_many(result.get("active_event_ids"))

    for entry in adjustment_log or []:
        if not isinstance(entry, dict):
            continue
        signal_id = entry.get("signal_id") or entry.get("id")
        if signal_id:
            add_many([{"id": signal_id}])

    return collected


def _normalize_approved_signal_payload(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    if isinstance(item, str):
        item = {"id": item}
    if not isinstance(item, dict):
        return None

    signal_id = str(item.get("id") or item.get("signal_id") or "").strip()
    if not signal_id:
        return None

    key_players = item.get("key_players", [])
    if isinstance(key_players, str):
        key_players = [key_players]
    if not isinstance(key_players, list):
        key_players = []

    payload = dict(item)
    payload["id"] = signal_id
    payload["signal_type"] = str(payload.get("signal_type") or "unknown")
    payload["impact_direction"] = str(payload.get("impact_direction") or "neutral")
    payload["summary_zh"] = str(payload.get("summary_zh") or "")
    payload["source_reliability"] = float(payload.get("source_reliability") or 0.0)
    payload["confidence"] = float(payload.get("confidence") or 0.0)
    payload["key_players"] = [str(player) for player in key_players if player]
    if "team" not in payload and payload.get("team_name"):
        payload["team"] = payload.get("team_name")
    return payload


async def _hydrate_approved_signals_from_db(
    signals: list[dict[str, Any]],
    db: Any,
) -> list[dict[str, Any]]:
    """Hydrate signal IDs with DB evidence when available.

    This is best-effort: unresolved IDs remain in the payload with safe defaults
    so downstream post-match signal evaluation can still detect the missing link.
    """
    if not signals:
        return []
    signal_ids = [str(signal.get("id")) for signal in signals if signal.get("id")]
    if not signal_ids:
        return signals
    try:
        from sqlalchemy import text

        placeholders = ", ".join(f":sig_{idx}" for idx, _ in enumerate(signal_ids))
        params = {f"sig_{idx}": signal_id for idx, signal_id in enumerate(signal_ids)}
        result = await db.execute(
            text(
                f"""
                SELECT ns.id, ns.team_id, t.name AS team, ns.signal_type,
                       ns.impact_direction, ns.evidence_id, ns.confidence,
                       ns.summary_zh, ns.player_name, ns.claim,
                       ns.evidence_snippet, ns.normalized_availability,
                       ns.expected_minutes_delta, ns.effective_until,
                       ns.contradiction_risk, ns.conflict_group_id,
                       ns.source_reliability, ns.reviewed_at, ns.key_players
                FROM news_signals ns
                LEFT JOIN teams t ON t.id = ns.team_id
                WHERE ns.id IN ({placeholders})
                """
            ),
            params,
        )
        rows = result.mappings().all()
    except Exception:
        logging.getLogger(__name__).debug("approved signal hydration skipped", exc_info=True)
        return signals

    by_id = {str(row["id"]): row for row in rows}
    hydrated: list[dict[str, Any]] = []
    for signal in signals:
        signal_id = str(signal.get("id") or "")
        row = by_id.get(signal_id)
        if row is None:
            missing = dict(signal)
            missing.setdefault("source_status", "missing_news_signal")
            hydrated.append(_normalize_approved_signal_payload(missing) or missing)
            continue
        payload = {
            "id": signal_id,
            "team_id": str(row["team_id"]) if row["team_id"] else None,
            "team": row["team"],
            "signal_type": row["signal_type"],
            "impact_direction": row["impact_direction"],
            "evidence_id": row["evidence_id"],
            "confidence": row["confidence"],
            "summary_zh": row["summary_zh"],
            "player_name": row["player_name"],
            "claim": row["claim"],
            "evidence_snippet": row["evidence_snippet"],
            "normalized_availability": row["normalized_availability"],
            "expected_minutes_delta": row["expected_minutes_delta"],
            "effective_until": row["effective_until"],
            "contradiction_risk": row["contradiction_risk"],
            "conflict_group_id": row["conflict_group_id"],
            "source_reliability": row["source_reliability"],
            "reviewed_at": row["reviewed_at"],
            "key_players": _json_load(row["key_players"], []),
        }
        hydrated.append(_normalize_approved_signal_payload({**signal, **payload}) or payload)
    return hydrated


def _build_snapshot_pipeline_params(
    pipeline: dict[str, Any],
    meta: dict[str, Any],
    prediction: dict[str, Any],
    evaluation_sample: dict[str, Any],
) -> dict[str, Any]:
    return {
        "dc_converged": pipeline.get("dc_converged"),
        "dc_nll": pipeline.get("dc_nll"),
        "enhancer_algorithm": pipeline.get("enhancer_algorithm"),
        "enhancer_rows": pipeline.get("enhancer_rows"),
        "enhancer_features": pipeline.get("enhancer_features"),
        "elo_matches": pipeline.get("elo_matches"),
        "training_rows": pipeline.get("training_rows", meta.get("training_rows")),
        "weight_config": deepcopy(meta.get("weight_config") or {}),
        "pre_market_probs": deepcopy(pipeline.get("pre_market_probs")),
        "market_weight_used": float(
            pipeline.get(
                "market_weight_used",
                prediction.get("market_weight_used", 0.0),
            )
            or 0.0
        ),
        "negbin_applied": bool(prediction.get("negbin_applied", False)),
        "negbin_weight": 0.05 if prediction.get("negbin_applied") else 0.0,
        "calibration_applied": bool(
            pipeline.get("calibration_applied", False)
        ),
        "evaluation_sample": evaluation_sample,
    }


def _build_prediction_run_feature_snapshot(
    result: dict[str, Any],
    adjustment_log: list[dict[str, Any]],
    evaluation_sample: dict[str, Any],
    approved_signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    meta = result["meta"]
    pipeline = result.get("pipeline") or result.get("pipeline_params") or {}
    return {
        "training_rows": pipeline.get("training_rows", meta.get("training_rows")),
        "match_context": {
            "home_team_name": meta["home_team"],
            "away_team_name": meta["away_team"],
            "competition": meta["competition"],
            "is_neutral": meta.get("is_neutral", False),
        },
        "adjustment_log": adjustment_log,
        "approved_signal_ids": [
            signal.get("id") for signal in (approved_signals or []) if signal.get("id")
        ],
        "prediction_mode": "script_snapshot",
        "source": "snapshot.py -> save_prediction_snapshot()",
        "weight_config": deepcopy(meta.get("weight_config") or {}),
        "pre_market_probs": deepcopy(pipeline.get("pre_market_probs")),
        "market_weight_used": float(
            pipeline.get(
                "market_weight_used",
                (result.get("prediction") or {}).get("market_weight_used", 0.0),
            )
            or 0.0
        ),
        "calibration_applied": bool(
            pipeline.get(
                "calibration_applied",
                result.get("calibration_applied", False),
            )
        ),
        "evaluation_sample": evaluation_sample,
    }


def _build_score_matrix(home_xg: float, away_xg: float, max_goals: int = 5) -> list[list[float]]:
    """Reconstruct Poisson score probability matrix from xG values."""
    def _poisson_pmf(goals: int, rate: float) -> float:
        rate = max(float(rate), 1e-8)
        return math.exp(goals * math.log(rate) - rate - math.lgamma(goals + 1))

    size = max_goals + 1
    matrix = np.zeros((size, size))
    for hg in range(size):
        for ag in range(size):
            matrix[hg, ag] = _poisson_pmf(hg, float(home_xg)) * _poisson_pmf(ag, float(away_xg))
    total = float(matrix.sum())
    if total > 0:
        matrix = matrix / total
    return matrix.tolist()


def _json_dumps(obj: Any) -> str:
    """JSON-serialize an object to string for raw SQL insertion."""
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)


def _json_load(raw: Any, default: Any = None) -> Any:
    import json
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default
